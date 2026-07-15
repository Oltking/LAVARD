"""DIRECTORY (spec §5.7) — TheHouse's storefront data: every reachable ASP with slashed
prices, mode badges, and live stats. non_aggregatable ASPs are listed (never hidden) with
a DIRECT ROUTE badge for single-access-point convenience."""

from __future__ import annotations

import html as _html
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine

from thehouse.core.models import ASPMode
from thehouse.core.storage.db import asp_registry, economics_ledger, request_log

BADGES = {
    ASPMode.A_LLM: "AGGREGATED",
    ASPMode.B_NATIVE: "AGGREGATED",
    ASPMode.B_FANOUT: "PARALLEL ROUTE — REDUCED FEE",
    ASPMode.NON_AGGREGATABLE: "DIRECT ROUTE — NO DISCOUNT",
    ASPMode.MANUAL_REVIEW: "DIRECT ROUTE — NO DISCOUNT",
}


class DirectoryService:
    def __init__(self, engine: AsyncEngine):
        self.engine = engine

    async def listing(self) -> list[dict[str, Any]]:
        A, R, L = asp_registry.c, request_log.c, economics_ledger.c
        async with self.engine.connect() as conn:
            entries = (
                await conn.execute(select(asp_registry).where(A.active.is_(True)))
            ).mappings().all()
            volumes = dict(
                (await conn.execute(select(R.asp_id, func.count()).group_by(R.asp_id))).all()
            )
            avg_window = dict(
                (
                    await conn.execute(
                        select(L.asp_id, func.avg(L.window_open_ms)).group_by(L.asp_id)
                    )
                ).all()
            )

        from thehouse.core.models import ASPEntry
        from thehouse.core.pricing import caller_price

        rows = []
        for e in entries:
            mode = ASPMode(e["mode"])
            price = caller_price(ASPEntry(**dict(e)))
            rows.append(
                {
                    "asp_id": e["asp_id"],
                    "tool_name": e["tool_name"],
                    "description": e["description"],
                    "badge": BADGES[mode],
                    "mode": mode.value,
                    "original_price": e["original_price_per_call"],
                    "thehouse_price": price,
                    "discounted": mode in (ASPMode.A_LLM, ASPMode.B_NATIVE),
                    "call_volume": volumes.get(e["asp_id"], 0),
                    "avg_added_latency_ms": round(avg_window.get(e["asp_id"]) or 0.0, 1),
                }
            )
        rows.sort(key=lambda r: r["call_volume"], reverse=True)
        return rows


# ---------------------------------------------------------------------------
# Storefront rendering — same monochrome identity as the landing page (GET /):
# black ink on white, inverting bands, engraved serif display, sharp mono
# buttons. Shares BASE_CSS and the seal with directory/landing.py.
# Served by core.api at GET /directory.
# ---------------------------------------------------------------------------

_DIR_CSS = """
/* ---- directory page ---- */
.dir-hero{text-align:center; padding:56px 0 48px}
.dir-hero .seal{width:84px; height:84px; margin:0 auto 26px; display:block}
h1.dir-title{
  font-family:var(--display); font-weight:400; margin:20px 0 0;
  font-size:clamp(2.4rem,7.5vw,4.6rem); line-height:1; letter-spacing:.1em;
  text-transform:uppercase; text-wrap:balance;
}
.dir-hero .sub{
  font-family:var(--display); font-style:italic; color:var(--grey);
  font-size:clamp(1rem,2.2vw,1.3rem); max-width:34em; margin:22px auto 0; text-wrap:balance;
}

/* ---- register controls ---- */
.reg-head{display:flex; align-items:baseline; justify-content:space-between; gap:18px; flex-wrap:wrap; margin:64px 0 18px}
.reg-head .eyebrow{margin:0}
.search{
  font-family:var(--mono); font-size:.78rem; letter-spacing:.08em; color:var(--ink);
  background:var(--paper); border:1px solid var(--ink); border-radius:0;
  padding:12px 16px; width:min(340px,100%);
}
.search::placeholder{color:var(--grey); text-transform:uppercase; letter-spacing:.18em; font-size:.64rem}
.search:focus-visible{outline:2px solid var(--ink); outline-offset:2px}

/* ---- the register ---- */
.board{border:1px solid var(--ink)}
.brow{
  display:grid; grid-template-columns:minmax(0,1fr) 190px 84px 96px 168px;
  gap:0 20px; align-items:center; padding:22px 24px; border-top:1px solid var(--hair);
}
.brow:first-child{border-top:0}
.brow.head{
  font-family:var(--mono); font-size:.6rem; letter-spacing:.24em; text-transform:uppercase;
  color:var(--grey); padding-block:13px; border-top:0; border-bottom:1px solid var(--ink);
}
.svc .name{font-family:var(--display); font-size:1.3rem; font-weight:400; letter-spacing:.04em; margin:0}
.svc .tool{font-family:var(--mono); font-size:.68rem; letter-spacing:.06em; color:var(--grey); margin:4px 0 6px; word-break:break-all}
.svc .desc{color:var(--grey); font-size:.86rem; margin:0; max-width:44em}
.chip{
  display:inline-block; font-family:var(--mono); font-size:.58rem; letter-spacing:.16em;
  text-transform:uppercase; padding:6px 10px; border:1px solid var(--ink); white-space:nowrap;
}
.chip.agg{background:var(--ink); color:var(--paper)}
.num{font-family:var(--mono); font-variant-numeric:tabular-nums; font-size:.84rem; color:var(--ink)}
.num small{display:block; color:var(--grey); font-size:.58rem; letter-spacing:.16em; text-transform:uppercase; margin-top:3px}
.price{text-align:right; font-variant-numeric:tabular-nums}
.price .was{font-family:var(--mono); font-size:.72rem; color:var(--grey); text-decoration:line-through}
.price .now{font-family:var(--mono); font-size:1.18rem; font-weight:700; color:var(--ink); display:block; line-height:1.3}
.price .save{font-family:var(--mono); font-size:.6rem; letter-spacing:.14em; text-transform:uppercase; color:var(--grey)}
.empty{padding:64px 24px; text-align:center; color:var(--grey); font-family:var(--display); font-style:italic; font-size:1.15rem}

/* ---- motion ---- */
@media (prefers-reduced-motion: no-preference){
  .brow:not(.head){animation:rise .5s cubic-bezier(.2,.6,.2,1) both}
  .brow:nth-child(2){animation-delay:.04s}.brow:nth-child(3){animation-delay:.08s}
  .brow:nth-child(4){animation-delay:.12s}.brow:nth-child(5){animation-delay:.16s}
  .brow:nth-child(6){animation-delay:.2s}.brow:nth-child(7){animation-delay:.24s}
  @keyframes rise{from{opacity:0; transform:translateY(8px)}to{opacity:1; transform:none}}
}

/* ---- narrow ---- */
@media (max-width:860px){
  .brow{grid-template-columns:minmax(0,1fr) auto; grid-template-areas:"svc price" "route price" "vol lat"; row-gap:12px}
  .brow.head{display:none}
  .svc{grid-area:svc}.route{grid-area:route}.price{grid-area:price; align-self:start}
  .vol{grid-area:vol}.lat{grid-area:lat}
}
"""

_JS = """
(function(){
  var q = document.getElementById('q');
  if(q){
    q.addEventListener('input', function(){
      var needle = q.value.toLowerCase();
      document.querySelectorAll('.board .brow:not(.head)').forEach(function(row){
        row.style.display = row.textContent.toLowerCase().indexOf(needle) >= 0 ? '' : 'none';
      });
    });
  }
  // live refresh when served by TheHouse itself; silently inert elsewhere (CSP/preview)
  var main = document.querySelector('main');
  if(main && main.dataset.live === '1'){
    setInterval(function(){
      fetch('/v1/directory').then(function(r){return r.json()}).then(function(rows){
        var total = 0;
        rows.forEach(function(r){ total += r.call_volume; });
        var el = document.getElementById('t-calls');
        if(el) el.textContent = total.toLocaleString();
      }).catch(function(){});
    }, 5000);
  }
})();
"""


def _row_html(r: dict[str, Any]) -> str:
    esc = _html.escape
    save_pct = (
        round((1 - r["thehouse_price"] / r["original_price"]) * 100)
        if r["original_price"] and r["discounted"]
        else 0
    )
    if r["discounted"]:
        price = (
            f'<span class="was">{r["original_price"]:.2f} USDT</span>'
            f'<span class="now">{r["thehouse_price"]:.2f} USDT</span>'
            f'<span class="save">save {save_pct}% every call</span>'
        )
    else:
        price = f'<span class="now">{r["thehouse_price"]:.2f} USDT</span>'
    chip = "chip agg" if r["discounted"] else "chip"
    return f"""
    <div class="brow">
      <div class="svc">
        <h3 class="name">{esc(r["asp_id"])}</h3>
        <p class="tool">{esc(r["tool_name"])}</p>
        <p class="desc">{esc(r["description"] or "")}</p>
      </div>
      <div class="route"><span class="{chip}">{esc(r["badge"])}</span></div>
      <div class="vol num">{r["call_volume"]:,}<small>calls</small></div>
      <div class="lat num">+{r["avg_added_latency_ms"]:g} ms<small>batching</small></div>
      <div class="price">{price}</div>
    </div>"""


def render_body(rows: list[dict[str, Any]], live: bool = True) -> str:
    """Page body + styles (no document shell) — reused by the artifact preview."""
    from thehouse.directory.landing import BASE_CSS, SEAL

    n_services = len(rows)
    total_calls = sum(r["call_volume"] for r in rows)
    discounted = [r for r in rows if r["discounted"]]
    avg_disc = (
        round(
            sum(1 - r["thehouse_price"] / r["original_price"] for r in discounted)
            / len(discounted)
            * 100
        )
        if discounted
        else 0
    )
    latencies = [r["avg_added_latency_ms"] for r in rows if r["avg_added_latency_ms"]]
    avg_lat = round(sum(latencies) / len(latencies)) if latencies else 0

    body_rows = "".join(_row_html(r) for r in rows) or (
        '<div class="empty">The board is empty — onboard a target ASP to open the first market.</div>'
    )

    seal_mark = SEAL.format(cls="mark", uid="m")
    seal_hero = SEAL.format(cls="seal", uid="h")
    seal_foot = SEAL.format(cls="mark", uid="f")

    return f"""<title>TheHouse — the directory of listed services</title>
<style>{BASE_CSS}{_DIR_CSS}</style>

<div class="wrap">
  <header class="topbar">
    <a class="brand" href="/">{seal_mark}<span class="name">TheHouse</span></a>
    <nav class="links" aria-label="primary">
      <a href="/#articles">Doctrine</a>
      <a href="/#economics">Economics</a>
      <a href="/#rules">House rules</a>
      <a class="btn ghost" href="/">Return to the House</a>
    </nav>
  </header>
  <hr class="rules-double">
</div>

<main {'data-live="1"' if live else ""}>
  <section class="dir-hero wrap">
    {seal_hero}
    <p class="eyebrow">The register of listed services</p>
    <h1 class="dir-title">The Directory</h1>
    <p class="sub">Every target the desk reaches, at its standing price.
    Same interface as the original — change only the endpoint.</p>
  </section>

  <section class="band" aria-label="figures of record">
    <div class="wrap">
      <div class="stat"><span class="n">{n_services}</span><span class="l">services listed</span></div>
      <div class="stat"><span class="n" id="t-calls">{total_calls:,}</span><span class="l">calls served</span></div>
      <div class="stat"><span class="n">−{avg_disc}%</span><span class="l">average discount</span></div>
      <div class="stat"><span class="n">+{avg_lat} ms</span><span class="l">added latency</span></div>
    </div>
  </section>

  <section class="wrap" aria-label="service register" style="padding-bottom:88px">
    <div class="reg-head">
      <p class="eyebrow">Entered into the register</p>
      <input class="search" id="q" type="search" placeholder="Filter the register…" aria-label="Filter services">
    </div>
    <div class="board">
      <div class="brow head">
        <div>Service</div><div>Route</div><div>Volume</div><div>Latency</div>
        <div style="text-align:right">Price / call</div>
      </div>
      {body_rows}
    </div>
  </section>
</main>

<footer class="wrap">
  <hr class="rules-double">
  <div class="foot">
    <a class="brand" href="/">{seal_foot}<span class="fine">TheHouse · Agent Service Provider</span></a>
    <span class="fine">Every discount backed by a real saving · Pay per call · x402 · X Layer</span>
  </div>
</footer>
<script>{_JS}</script>"""


def render_html(rows: list[dict[str, Any]], live: bool = True) -> str:
    """Full document, served by core.api at GET /directory."""
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        "</head><body>" + render_body(rows, live=live) + "</body></html>"
    )
