"""ECONOMICS ENGINE (spec §5.6): weekly report + nightly auto-protection rules.

Every batch already lands in `economics_ledger` (written by the pipeline); cache hits land
in `request_log` with status=cached and the charged price. This module reads both.

Auto-protection (run nightly):
- ASP with >30% below-break-even batches in the last 7 days → window_timer_ms × 1.5
- ASP with >60% → demoted to PARALLEL ROUTE (mode=B_fanout, margin promise removed)
- ASP with any split_quality=failed in the last 24h → re-profiled; a Mode A ASP that
  failed twice → manual_review
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncEngine

from thehouse.core.models import ASPMode, now_ms
from thehouse.core.profiler.profiler import Profiler
from thehouse.core.profiler.registry import RegistryService
from thehouse.core.storage.db import (
    asp_registry,
    audit,
    economics_ledger,
    request_log,
    settlements,
)

WEEK_MS = 7 * 24 * 3600 * 1000
DAY_MS = 24 * 3600 * 1000


@dataclass
class Reconciliation:
    """On-chain settlements vs the internal ledger, so money drift fails loudly (audit fix #4)."""

    since_ms: int
    inbound_settled: float = 0.0      # Σ settlements("in")  — what callers actually paid on-chain
    inbound_charged: float = 0.0      # Σ request_log.charged — what TheHouse billed
    outbound_settled: float = 0.0     # Σ settlements("out") — what TheHouse paid targets on-chain
    outbound_cost: float = 0.0        # Σ economics_ledger.target_cost_paid — what it recorded owing
    tolerance: float = 1e-6

    @property
    def inbound_delta(self) -> float:
        return round(self.inbound_settled - self.inbound_charged, 6)

    @property
    def outbound_delta(self) -> float:
        return round(self.outbound_settled - self.outbound_cost, 6)

    @property
    def inbound_balanced(self) -> bool:
        return abs(self.inbound_delta) <= self.tolerance

    @property
    def outbound_balanced(self) -> bool:
        return abs(self.outbound_delta) <= self.tolerance

    @property
    def balanced(self) -> bool:
        # Both faces must settle on-chain to be fully balanced (the outbound face only settles when
        # a buyer PaymentHook is wired AND the target actually charges via 402 — a free target
        # legitimately shows outbound_cost with zero outbound settlement).
        return self.inbound_balanced and self.outbound_balanced

    def render(self) -> str:
        flag = "OK" if self.balanced else "DRIFT"
        return (
            f"TheHouse — Settlement Reconciliation [{flag}]\n"
            f"  inbound : settled {self.inbound_settled:.6f} vs charged {self.inbound_charged:.6f} "
            f"(Δ {self.inbound_delta:+.6f})\n"
            f"  outbound: settled {self.outbound_settled:.6f} vs cost {self.outbound_cost:.6f} "
            f"(Δ {self.outbound_delta:+.6f})"
        )


@dataclass
class ProtectionAction:
    asp_id: str
    action: str          # "window_extended" | "demoted_parallel" | "reprofiled" | "manual_review"
    detail: str = ""


@dataclass
class WeeklyReport:
    since_ms: int
    total_gross_margin: float = 0.0
    dedup_savings: float = 0.0
    priority_surcharge_revenue: float = 0.0
    below_break_even_by_asp: dict[str, int] = field(default_factory=dict)
    top_by_volume: list[tuple[str, int]] = field(default_factory=list)
    top_by_margin: list[tuple[str, float]] = field(default_factory=list)
    avg_batch_fill_rate: float = 0.0

    def render(self) -> str:
        lines = [
            "TheHouse — Weekly Economics Report",
            "=" * 40,
            f"Total gross margin:          {self.total_gross_margin:.4f} USDT",
            f"Deduplication savings:       {self.dedup_savings:.4f} USDT (pure profit)",
            f"Priority surcharge revenue:  {self.priority_surcharge_revenue:.4f} USDT",
            f"Average batch fill rate:     {self.avg_batch_fill_rate:.0%}",
            "",
            "Below-break-even events by ASP:",
        ]
        lines += [f"  {a}: {n}" for a, n in self.below_break_even_by_asp.items()] or ["  none"]
        lines.append("\nTop 5 ASPs by call volume:")
        lines += [f"  {a}: {n} requests" for a, n in self.top_by_volume]
        lines.append("\nTop 5 ASPs by margin generated:")
        lines += [f"  {a}: {m:.4f} USDT" for a, m in self.top_by_margin]
        return "\n".join(lines)


class EconomicsEngine:
    def __init__(self, engine: AsyncEngine):
        self.engine = engine
        self.registry = RegistryService(engine)

    async def weekly_report(self, since_ms: int | None = None) -> WeeklyReport:
        since = since_ms if since_ms is not None else now_ms() - WEEK_MS
        report = WeeklyReport(since_ms=since)
        L, R, A = economics_ledger.c, request_log.c, asp_registry.c

        async with self.engine.connect() as conn:
            row = (
                await conn.execute(
                    select(
                        func.coalesce(func.sum(L.gross_margin), 0.0),
                        func.coalesce(func.sum(L.priority_surcharges), 0.0),
                    ).where(L.created_at_ms >= since)
                )
            ).first()
            report.total_gross_margin = round(row[0], 6)
            report.priority_surcharge_revenue = round(row[1], 6)

            # dedup savings: cache-hit requests × the target price TheHouse did NOT pay,
            # plus in-batch merges (ledger dedup_hits) × target price.
            cached = (
                await conn.execute(
                    select(R.asp_id, func.count())
                    .where(R.status == "cached", R.received_at_ms >= since)
                    .group_by(R.asp_id)
                )
            ).all()
            merges = (
                await conn.execute(
                    select(L.asp_id, func.coalesce(func.sum(L.dedup_hits), 0))
                    .where(L.created_at_ms >= since)
                    .group_by(L.asp_id)
                )
            ).all()
            prices = {
                r[0]: r[1]
                for r in (
                    await conn.execute(select(A.asp_id, A.original_price_per_call))
                ).all()
            }
            savings = sum(prices.get(a, 0.0) * n for a, n in cached)
            savings += sum(prices.get(a, 0.0) * n for a, n in merges)
            report.dedup_savings = round(savings, 6)

            bbe = (
                await conn.execute(
                    select(L.asp_id, func.count())
                    .where(L.below_break_even.is_(True), L.created_at_ms >= since)
                    .group_by(L.asp_id)
                )
            ).all()
            report.below_break_even_by_asp = dict(bbe)

            report.top_by_volume = [
                (a, n)
                for a, n in (
                    await conn.execute(
                        select(R.asp_id, func.count())
                        .where(R.received_at_ms >= since)
                        .group_by(R.asp_id)
                        .order_by(func.count().desc())
                        .limit(5)
                    )
                ).all()
            ]
            report.top_by_margin = [
                (a, round(m, 6))
                for a, m in (
                    await conn.execute(
                        select(L.asp_id, func.sum(L.gross_margin))
                        .where(L.created_at_ms >= since)
                        .group_by(L.asp_id)
                        .order_by(func.sum(L.gross_margin).desc())
                        .limit(5)
                    )
                ).all()
            ]

            fill = (
                await conn.execute(
                    select(func.avg(L.batch_size * 1.0 / A.max_batch_size))
                    .select_from(economics_ledger.join(asp_registry, L.asp_id == A.asp_id))
                    .where(L.created_at_ms >= since)
                )
            ).scalar()
            report.avg_batch_fill_rate = round(fill or 0.0, 4)

        return report

    async def reconcile_settlements(self, since_ms: int | None = None) -> Reconciliation:
        """Cross-check the on-chain settlements table against the internal ledgers. Any drift
        (a dropped receipt, a charge that didn't settle) surfaces as a nonzero delta instead of
        going silent. Only gateway-originated, payment-bearing traffic settles on-chain, so this
        balances for the real-money path (operator REST intake has no settlement rows)."""
        rec = Reconciliation(since_ms=since_ms if since_ms is not None else now_ms() - WEEK_MS)
        S, R, L = settlements.c, request_log.c, economics_ledger.c
        async with self.engine.connect() as conn:
            # inbound_settled = money actually COLLECTED on-chain (status "settled"), NOT merely
            # invoiced. A charge created but not yet paid (status "pending") is excluded, so an
            # uncollected charge surfaces as drift instead of falsely balancing (audit HIGH-1).
            rec.inbound_settled = round((await conn.execute(
                select(func.coalesce(func.sum(S.amount_usdt), 0.0))
                .where(S.direction == "in", S.settle_status == "settled", S.ts_ms >= rec.since_ms)
            )).scalar() or 0.0, 6)
            # inbound_charged = what we billed for every request we INVOICED (has any inbound row,
            # settled or not). billed > collected ⇒ uncollected-revenue drift.
            paid_ids = select(S.request_id).where(S.direction == "in", S.request_id.isnot(None))
            rec.inbound_charged = round((await conn.execute(
                select(func.coalesce(func.sum(R.charged), 0.0))
                .where(R.charged.isnot(None), R.request_id.in_(paid_ids))
            )).scalar() or 0.0, 6)
            rec.outbound_settled = round((await conn.execute(
                select(func.coalesce(func.sum(S.amount_usdt), 0.0))
                .where(S.direction == "out", S.ts_ms >= rec.since_ms)
            )).scalar() or 0.0, 6)
            rec.outbound_cost = round((await conn.execute(
                select(func.coalesce(func.sum(L.target_cost_paid), 0.0))
                .where(L.created_at_ms >= rec.since_ms)
            )).scalar() or 0.0, 6)
        if not rec.inbound_balanced:
            # inbound drift = callers billed ≠ callers paid on-chain — the money face that must
            # always reconcile. (Outbound depends on whether targets 402 + a buyer hook is wired.)
            import logging
            logging.getLogger("thehouse.economics").warning(
                "ALERT settlement_drift inbound_delta=%.6f outbound_delta=%.6f",
                rec.inbound_delta, rec.outbound_delta)
            await audit("settlement_drift", {
                "inbound_delta": rec.inbound_delta, "outbound_delta": rec.outbound_delta,
            }, engine=self.engine)
        return rec

    async def run_auto_protection(self, profiler: Profiler | None = None) -> list[ProtectionAction]:
        """Nightly job. Returns the actions taken (also audited)."""
        actions: list[ProtectionAction] = []
        L = economics_ledger.c
        week_ago, day_ago = now_ms() - WEEK_MS, now_ms() - DAY_MS

        for entry in await self.registry.list_all():
            async with self.engine.connect() as conn:
                total = (
                    await conn.execute(
                        select(func.count()).where(
                            L.asp_id == entry.asp_id, L.created_at_ms >= week_ago
                        )
                    )
                ).scalar()
                below = (
                    await conn.execute(
                        select(func.count()).where(
                            L.asp_id == entry.asp_id,
                            L.below_break_even.is_(True),
                            L.created_at_ms >= week_ago,
                        )
                    )
                ).scalar()
                failed_24h = (
                    await conn.execute(
                        select(func.count()).where(
                            L.asp_id == entry.asp_id,
                            L.split_quality == "failed",
                            L.created_at_ms >= day_ago,
                        )
                    )
                ).scalar()

            if total:
                ratio = below / total
                if ratio > 0.60 and entry.mode in (ASPMode.A_LLM, ASPMode.B_NATIVE):
                    await self.registry.set_mode(entry.asp_id, ASPMode.B_FANOUT.value)
                    actions.append(
                        ProtectionAction(entry.asp_id, "demoted_parallel", f"{ratio:.0%} below break-even")
                    )
                elif ratio > 0.30:
                    new_timer = int(entry.window_timer_ms * 1.5)
                    async with self.engine.begin() as conn:
                        await conn.execute(
                            update(asp_registry)
                            .where(asp_registry.c.asp_id == entry.asp_id)
                            .values(window_timer_ms=new_timer)
                        )
                    actions.append(
                        ProtectionAction(
                            entry.asp_id, "window_extended",
                            f"{ratio:.0%} below break-even → timer {entry.window_timer_ms}→{new_timer}ms",
                        )
                    )

            if failed_24h:
                if entry.mode == ASPMode.A_LLM and failed_24h >= 2:
                    await self.registry.set_mode(entry.asp_id, ASPMode.MANUAL_REVIEW.value)
                    actions.append(
                        ProtectionAction(entry.asp_id, "manual_review", f"{failed_24h} failed splits in 24h")
                    )
                elif profiler is not None:
                    await profiler.profile(entry)
                    await self.registry.upsert(entry)
                    actions.append(
                        ProtectionAction(entry.asp_id, "reprofiled", f"mode now {entry.mode.value}")
                    )

        for action in actions:
            await audit(
                "auto_protection",
                {"asp_id": action.asp_id, "action": action.action, "detail": action.detail},
                engine=self.engine,
            )
        return actions
