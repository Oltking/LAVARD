"""THE DESK — the operator dashboard, served at GET /desk (operator-only in prod).

What the owner needs at a glance: is money coming in, are batches filling, is any target
failing. Rendered in the same monochrome identity as the rest of the House."""

from __future__ import annotations

import html as _html
from typing import Any

from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncEngine

from thehouse.core.models import now_ms
from thehouse.core.storage.db import asp_registry, economics_ledger, request_log, settlements

DAY_MS = 24 * 3600 * 1000


async def gather(engine: AsyncEngine, redis: Any) -> dict[str, Any]:
    since = now_ms() - DAY_MS
    L, R, S, A = economics_ledger.c, request_log.c, settlements.c, asp_registry.c

    async with engine.connect() as conn:
        totals = (
            await conn.execute(
                select(
                    func.count(),
                    func.coalesce(func.sum(L.thehouse_revenue_collected), 0.0),
                    func.coalesce(func.sum(L.target_cost_paid), 0.0),
                    func.coalesce(func.sum(L.gross_margin), 0.0),
                    func.coalesce(func.avg(L.batch_size), 0.0),
                ).where(L.created_at_ms >= since)
            )
        ).first()
        statuses = dict(
            (
                await conn.execute(
                    select(R.status, func.count())
                    .where(R.received_at_ms >= since)
                    .group_by(R.status)
                )
            ).all()
        )
        # cache hits settle at intake and never reach the batch ledger — pure margin
        cached = (
            await conn.execute(
                select(func.coalesce(func.sum(R.charged), 0.0)).where(
                    R.received_at_ms >= since, R.status == "cached"
                )
            )
        ).scalar()
        trouble = (
            await conn.execute(
                select(L.asp_id, L.split_quality, func.count())
                .where(L.created_at_ms >= since, L.split_quality != "clean")
                .group_by(L.asp_id, L.split_quality)
            )
        ).all()
        below_be = dict(
            (
                await conn.execute(
                    select(L.asp_id, func.count())
                    .where(L.created_at_ms >= since, L.below_break_even.is_(True))
                    .group_by(L.asp_id)
                )
            ).all()
        )
        recent = (
            await conn.execute(
                select(L.batch_id, L.asp_id, L.batch_size, L.window_fire_reason,
                       L.gross_margin, L.split_quality, L.created_at_ms)
                .order_by(desc(L.created_at_ms))
                .limit(12)
            )
        ).mappings().all()
        settled = dict(
            (
                await conn.execute(
                    select(S.direction, func.coalesce(func.sum(S.amount_usdt), 0.0))
                    .where(S.ts_ms >= since)
                    .group_by(S.direction)
                )
            ).all()
        )
        active = (
            await conn.execute(select(A.asp_id).where(A.active.is_(True)))
        ).scalars().all()

    from thehouse.core.window.queue import ASPQueue

    queue = ASPQueue(redis)
    depths = {asp_id: await queue.size(asp_id) for asp_id in active}

    return {
        "batches": totals[0],
        "revenue": round(totals[1] + cached, 6),
        "cost": round(totals[2], 6),
        "margin": round(totals[3] + cached, 6),
        "cache_revenue": round(cached, 6),
        "avg_batch_size": round(totals[4], 2),
        "statuses": statuses,
        "trouble": trouble,
        "below_be": below_be,
        "recent": [dict(r) for r in recent],
        "settled_in": round(settled.get("in", 0.0), 6),
        "settled_out": round(settled.get("out", 0.0), 6),
        "depths": {k: v for k, v in depths.items() if v},
    }


_DESK_CSS = """
.desk-head{text-align:center; padding:48px 0 40px}
h1.desk-title{font-family:var(--display); font-weight:400; margin:14px 0 0;
  font-size:clamp(2rem,6vw,3.6rem); letter-spacing:.12em; text-transform:uppercase}
.desk-sub{font-family:var(--mono); font-size:.66rem; letter-spacing:.24em;
  text-transform:uppercase; color:var(--grey); margin:14px 0 0}
.panel{border:1px solid var(--ink); margin:0 0 36px}
.panel h2{font-family:var(--mono); font-size:.62rem; letter-spacing:.26em;
  text-transform:uppercase; font-weight:400; margin:0; padding:12px 18px;
  border-bottom:1px solid var(--ink)}
table.ledgerlike{width:100%; border-collapse:collapse; font-variant-numeric:tabular-nums}
table.ledgerlike th{font-family:var(--mono); font-size:.58rem; letter-spacing:.2em;
  text-transform:uppercase; font-weight:400; color:var(--grey); padding:11px 18px;
  border-bottom:1px solid var(--hair); text-align:right}
table.ledgerlike th:first-child{text-align:left}
table.ledgerlike td{font-family:var(--mono); font-size:.82rem; padding:10px 18px;
  border-bottom:1px solid var(--hair); text-align:right}
table.ledgerlike td:first-child{text-align:left}
table.ledgerlike tr:last-child td{border-bottom:0}
.ok{color:var(--grey)}
td.flag{font-weight:700}
.empty-line{padding:20px 18px; font-family:var(--display); font-style:italic; color:var(--grey)}
"""


def _esc(v: Any) -> str:
    return _html.escape(str(v))


def render(data: dict[str, Any]) -> str:
    from thehouse.directory.landing import BASE_CSS, SEAL

    seal = SEAL.format(cls="mark", uid="d")
    st = data["statuses"]
    served = st.get("delivered", 0) + st.get("cached", 0)
    fill = data["avg_batch_size"]

    trouble_rows = "".join(
        f"<tr><td>{_esc(a)}</td><td class='flag'>{_esc(q)}</td><td>{n}</td></tr>"
        for a, q, n in data["trouble"]
    ) or '<tr><td colspan="3" class="empty-line">No split trouble in the last day.</td></tr>'

    bbe_rows = "".join(
        f"<tr><td>{_esc(a)}</td><td>{n}</td></tr>" for a, n in data["below_be"].items()
    ) or '<tr><td colspan="2" class="empty-line">Every batch met break-even.</td></tr>'

    depth_rows = "".join(
        f"<tr><td>{_esc(a)}</td><td>{n}</td></tr>" for a, n in data["depths"].items()
    ) or '<tr><td colspan="2" class="empty-line">All windows clear.</td></tr>'

    recent_rows = "".join(
        f"<tr><td>{_esc(r['asp_id'])}</td><td>{r['batch_size']}</td>"
        f"<td>{_esc(r['window_fire_reason'])}</td>"
        f"<td>{r['gross_margin']:+.4f}</td><td>{_esc(r['split_quality'])}</td></tr>"
        for r in data["recent"]
    ) or '<tr><td colspan="5" class="empty-line">No batches settled yet.</td></tr>'

    return f"""<title>TheHouse — the desk</title>
<style>{BASE_CSS}{_DESK_CSS}</style>

<div class="wrap">
  <header class="topbar">
    <a class="brand" href="/">{seal}<span class="name">TheHouse</span></a>
    <nav class="links" aria-label="primary">
      <a href="/directory">Directory</a>
      <a href="/metrics">Metrics</a>
      <a class="btn ghost" href="/">Return to the House</a>
    </nav>
  </header>
  <hr class="rules-double">

  <section class="desk-head">
    <p class="eyebrow">Operator's ledger — last 24 hours</p>
    <h1 class="desk-title">The Desk</h1>
    <p class="desk-sub">refreshes with the ledger · not a public page</p>
  </section>
</div>

<section class="band" aria-label="figures of the day">
  <div class="wrap">
    <div class="stat"><span class="n">{data["margin"]:+.2f}</span><span class="l">gross margin, USDT</span></div>
    <div class="stat"><span class="n">{served:,}</span><span class="l">requests served</span></div>
    <div class="stat"><span class="n">{data["batches"]:,}</span><span class="l">batches settled</span></div>
    <div class="stat"><span class="n">{fill:g}</span><span class="l">avg batch size</span></div>
  </div>
</section>

<div class="wrap" style="padding-top:56px; padding-bottom:72px">
  <div class="panel">
    <h2>Money, last 24h</h2>
    <table class="ledgerlike">
      <tr><td>Collected from callers (incl. cache hits)</td><td>{data["revenue"]:.4f} USDT</td></tr>
      <tr><td>Paid to targets</td><td>{data["cost"]:.4f} USDT</td></tr>
      <tr><td>Settlements in / out (wallet)</td><td>{data["settled_in"]:.4f} / {data["settled_out"]:.4f} USDT</td></tr>
      <tr><td>Cache hits (pure margin)</td><td>{st.get("cached", 0)} · {data.get("cache_revenue", 0.0):.4f} USDT</td></tr>
      <tr><td>Failed / expired</td><td>{st.get("failed", 0)}</td></tr>
    </table>
  </div>

  <div class="panel">
    <h2>Split trouble by target</h2>
    <table class="ledgerlike"><tr><th>Target</th><th>Quality</th><th>Batches</th></tr>{trouble_rows}</table>
  </div>

  <div class="panel">
    <h2>Below break-even</h2>
    <table class="ledgerlike"><tr><th>Target</th><th>Batches</th></tr>{bbe_rows}</table>
  </div>

  <div class="panel">
    <h2>Open queues</h2>
    <table class="ledgerlike"><tr><th>Target</th><th>Waiting</th></tr>{depth_rows}</table>
  </div>

  <div class="panel">
    <h2>Recent batches</h2>
    <table class="ledgerlike">
      <tr><th>Target</th><th>Size</th><th>Fired by</th><th>Margin</th><th>Split</th></tr>
      {recent_rows}
    </table>
  </div>
</div>

<footer class="wrap">
  <hr class="rules-double">
  <div class="foot">
    <span class="fine">TheHouse · the desk · operator only</span>
    <span class="fine">every figure from the ledger — nothing estimated</span>
  </div>
</footer>"""


def render_html(data: dict[str, Any]) -> str:
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        "</head><body>" + render(data) + "</body></html>"
    )
