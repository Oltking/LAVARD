"""Batch pipeline: fired window → (compose | pack | fan-out | direct) → dispatch →
split → delivery → economics ledger + audit (spec §4).

This is the orchestrator behind Phases 5–9. Payment settlement is a Phase 11 hook inside
the Dispatcher; the pipeline accounts for costs/revenue in the ledger either way.
"""

from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger("thehouse.pipeline")
from dataclasses import dataclass, field

from sqlalchemy import insert, select, update
from sqlalchemy.ext.asyncio import AsyncEngine

from thehouse.core.composer.service import chunk, compose
from thehouse.core.pricing import caller_price, settled_price
from thehouse.core.deduplicator.service import DedupService
from thehouse.core.dispatcher.service import DispatchError, Dispatcher
from thehouse.core.models import (
    DEDUP_SAFE_MODES,
    ASPEntry,
    ASPMode,
    CallerRequest,
    RequestStatus,
    SplitQuality,
    now_ms,
)
from thehouse.core.profiler.profiler import ToolCallResult
from thehouse.core.splitter.service import SplitOutcome, split_keyed, split_numbered
from thehouse.core.storage.db import audit, economics_ledger, request_log
from thehouse.core.window.manager import Batch

# No refund rail exists on OKX.AI: charges are collected up-front at the 402 gate and are
# final. A partial split still delivers (full response to every caller in the batch) at the
# price already paid; the quality flag lands in the ledger and drives auto-protection.


@dataclass
class Delivery:
    request_id: str
    caller_id: str
    answer: str
    charged: float
    partial_split: bool = False


@dataclass
class BatchReport:
    batch_id: str
    asp_id: str
    deliveries: list[Delivery] = field(default_factory=list)
    target_cost_paid: float = 0.0
    split_quality: SplitQuality = SplitQuality.CLEAN
    dedup_hits: int = 0
    priority_surcharges: float = 0.0
    tier_size: int = 0        # true paying-caller count that set the price tier (fired + merged)

    @property
    def revenue(self) -> float:
        return round(sum(d.charged for d in self.deliveries), 6)

    @property
    def gross_margin(self) -> float:
        return round(self.revenue - self.target_cost_paid, 6)


class BatchPipeline:
    def __init__(self, engine: AsyncEngine, redis, dispatcher: Dispatcher):
        self.engine = engine
        self.redis = redis
        self.dispatcher = dispatcher
        self.dedup = DedupService(redis)

    async def process(self, batch: Batch, entry: ASPEntry) -> BatchReport:
        report = BatchReport(batch_id=batch.batch_id, asp_id=batch.asp_id)

        # Fire-time tier is set by the TRUE paying group: the fired requests PLUS any merged/deduped
        # callers who share a slot (they pay too). A single dispatch shared by 2+ payers is a real
        # aggregation and earns the discount.
        tier_size = await self._paying_count(batch, entry)
        report.tier_size = tier_size

        # Solo short-circuit (Model B): a single dispatch with no one to aggregate — route it
        # directly (no compose/split, so a mis-split can't re-dispatch and double-charge the target).
        if len(batch.requests) == 1 and tier_size == 1 and entry.mode in (ASPMode.A_LLM, ASPMode.B_NATIVE):
            await self._direct_fallback(batch.requests, entry, report, reason="solo_no_batch",
                                        tier_size=1)
            await self._deliver(batch, entry, report)
            await self._ledger(batch, entry, report)
            return report

        if entry.mode == ASPMode.A_LLM:
            await self._process_mode_a(batch, entry, report, tier_size)
        elif entry.mode == ASPMode.B_NATIVE:
            await self._process_b_native(batch, entry, report, tier_size)
        elif entry.mode == ASPMode.B_FANOUT:
            await self._process_fanout(batch, entry, report, tier_size)
        else:  # non_aggregatable / manual_review → direct route, no margin
            await self._process_direct(batch, entry, report, tier_size)

        await self._deliver(batch, entry, report)
        await self._ledger(batch, entry, report)
        return report

    async def _paying_count(self, batch: Batch, entry: ASPEntry) -> int:
        """Fired requests + their merged/deduped members = everyone who pays for this batch."""
        total = len(batch.requests)
        for req in batch.requests:
            total += len(await self.dedup.merged_members(entry.asp_id, req.request_id))
        return total

    # -- Mode A ---------------------------------------------------------------
    async def _process_mode_a(self, batch: Batch, entry: ASPEntry, report: BatchReport,
                              tier_size: int) -> None:
        # SECURITY: a query that tries to steer the shared compound (prompt injection) is pulled
        # OUT and dispatched in ISOLATION, so it cannot poison a peer's split answer. It pays solo
        # price (it forfeits the batch discount by not being safely aggregatable).
        from thehouse.core.composer.service import is_injection_risk

        risky = [r for r in batch.requests if is_injection_risk(r.query or "")]
        safe = [r for r in batch.requests if r not in risky]
        if risky:
            await audit("injection_isolated", {"asp_id": entry.asp_id, "count": len(risky)},
                        engine=self.engine)
            await self._direct_fallback(risky, entry, report, reason="injection_risk", tier_size=1)
        if not safe:
            return
        # The whole fired batch decides the tier: the callers came together as a group and the
        # aggregation saved money, even if dispatch is chunked to fit max_batch_size.
        for sub in chunk(safe, entry.max_batch_size):
            composed = compose(sub)
            try:
                result = await self.dispatcher.dispatch(entry, _query_args(entry, composed.prompt))
            except DispatchError:
                await self._direct_fallback(sub, entry, report, reason="dispatch_failed",
                                            tier_size=tier_size)
                continue
            report.target_cost_paid += entry.original_price_per_call
            if result.is_error or not result.text:
                await self._direct_fallback(sub, entry, report, reason="target_error",
                                            tier_size=tier_size)
                continue
            outcome = split_numbered(result.text, composed.order)
            await self._isolate_failed(sub, entry, outcome, report)
            self._collect(sub, entry, outcome, report, tier_size=tier_size)

    # -- Mode B native ----------------------------------------------------------
    async def _process_b_native(self, batch: Batch, entry: ASPEntry, report: BatchReport,
                                tier_size: int) -> None:
        from thehouse.core.packer.service import duplicate_map, pack  # Phase 7 subsystem

        packed = pack(entry, batch.requests)
        try:
            result = await self.dispatcher.dispatch(entry, packed.arguments)
        except DispatchError:
            await self._direct_fallback(batch.requests, entry, report, reason="dispatch_failed",
                                        tier_size=tier_size)
            return
        report.target_cost_paid += entry.original_price_per_call
        if result.structured is None:
            await self._direct_fallback(batch.requests, entry, report, reason="target_error",
                                        tier_size=tier_size)
            return
        outcome = split_keyed(result.structured, packed.key_map)
        await self._isolate_failed(batch.requests, entry, outcome, report)
        # requests that shared a packed value receive the same answer
        for owner, dupes in duplicate_map(entry, batch.requests).items():
            if owner in outcome.answers:
                for rid in dupes:
                    outcome.answers[rid] = outcome.answers[owner]
                    report.dedup_hits += 1
        self._collect(batch.requests, entry, outcome, report, tier_size=tier_size)

    # -- Mode B fan-out ----------------------------------------------------------
    async def _process_fanout(self, batch: Batch, entry: ASPEntry, report: BatchReport,
                              tier_size: int) -> None:
        async def one(req: CallerRequest) -> tuple[CallerRequest, ToolCallResult | None]:
            try:
                return req, await self.dispatcher.dispatch(entry, req.arguments)
            except DispatchError:
                return req, None

        results = await asyncio.gather(*(one(r) for r in batch.requests))
        answers: dict[str, str] = {}
        for req, result in results:
            report.target_cost_paid += entry.original_price_per_call
            if result is not None and not result.is_error:
                answers[req.request_id] = result.text or _to_json(result.structured)
        quality = (
            SplitQuality.CLEAN
            if len(answers) == len(batch.requests)
            else (SplitQuality.PARTIAL if answers else SplitQuality.FAILED)
        )
        self._collect(
            batch.requests, entry, SplitOutcome(quality, answers), report, fanout=True,
            tier_size=tier_size,
        )

    # -- Direct route (non-aggregatable) -----------------------------------------
    async def _process_direct(self, batch: Batch, entry: ASPEntry, report: BatchReport,
                              tier_size: int) -> None:
        await self._direct_fallback(batch.requests, entry, report, reason="non_aggregatable",
                                    tier_size=tier_size)

    async def _direct_fallback(
        self, requests: list[CallerRequest], entry: ASPEntry, report: BatchReport, reason: str,
        tier_size: int | None = None,
    ) -> None:
        """Route each request individually at cost — no margin taken (spec rule 4 / §5.4)."""
        await audit("direct_route", {"asp_id": entry.asp_id, "reason": reason,
                                     "count": len(requests)}, engine=self.engine)
        for req in requests:
            try:
                result = await self.dispatcher.dispatch(entry, req.arguments)
            except DispatchError:
                continue
            report.target_cost_paid += entry.original_price_per_call
            answer = result.text or _to_json(result.structured)
            if answer is not None and not result.is_error:
                # settle the fire-time tier for however many shared this route: non-aggregatable →
                # original; a fallen-back aggregated batch → its solo/batched tier by size
                size = tier_size if tier_size is not None else len(requests)
                price = settled_price(entry, size, priority=req.priority)
                if req.priority:
                    report.priority_surcharges += round(
                        price - settled_price(entry, size), 6)
                report.deliveries.append(
                    Delivery(
                        request_id=req.request_id,
                        caller_id=req.caller_id,
                        answer=answer,
                        charged=price,
                    )
                )

    # -- privacy-preserving isolation of failed/partial splits ---------------------
    async def _isolate_failed(
        self, requests: list[CallerRequest], entry: ASPEntry, outcome: SplitOutcome,
        report: BatchReport,
    ) -> None:
        """Never hand a caller the compound blob — it carries other callers' questions and
        answers (a cross-caller data leak). For each caller whose segment could not be isolated
        (`outcome.full_ids`), re-dispatch their OWN request individually to get an isolated,
        correct, cacheable answer, at the price already collected. The extra target call eats
        margin — chronic offenders get demoted by auto-protection. If the isolated retry also
        fails, deliver a clear failure notice, never another caller's data. The split-quality
        flag is left FAILED/PARTIAL so auto-protection still sees the compound failure."""
        if not outcome.full_ids:
            return
        by_id = {r.request_id: r for r in requests}
        for rid in list(outcome.full_ids):
            req = by_id.get(rid)
            if req is None:
                continue
            try:
                result = await self.dispatcher.dispatch(entry, req.arguments)
            except DispatchError:
                result = None
            if result is not None and not result.is_error:
                answer = result.text or _to_json(result.structured)
                if answer:
                    report.target_cost_paid += entry.original_price_per_call
                    outcome.answers[rid] = answer
                    outcome.full_ids.discard(rid)   # isolated own answer → cacheable, no leak
                    continue
            outcome.answers[rid] = (
                f"delivery failed for request {rid}: the provider's batched response could not "
                f"be split and an isolated retry did not succeed. Contact the operator with this "
                f"request id."
            )

    # -- shared collection/delivery ------------------------------------------------
    def _collect(
        self,
        requests: list[CallerRequest],
        entry: ASPEntry,
        outcome: SplitOutcome,
        report: BatchReport,
        fanout: bool = False,
        tier_size: int | None = None,
    ) -> None:
        if outcome.quality != SplitQuality.CLEAN and report.split_quality == SplitQuality.CLEAN:
            report.split_quality = outcome.quality
        if outcome.quality == SplitQuality.FAILED:
            report.split_quality = SplitQuality.FAILED

        for req in requests:
            answer = outcome.answers.get(req.request_id)
            if answer is None:
                continue
            # Fire-time tier: the whole fired batch's paying count decides solo vs batched.
            size = tier_size if tier_size is not None else len(requests)
            price = settled_price(entry, size, priority=req.priority)
            if req.priority:
                report.priority_surcharges += round(price - settled_price(entry, size), 6)
            report.deliveries.append(
                Delivery(
                    request_id=req.request_id,
                    caller_id=req.caller_id,
                    answer=answer,
                    charged=price,
                    # per-request: only full-compound-text deliveries are "partial"
                    # (never cached); cleanly isolated segments are real answers
                    partial_split=req.request_id in outcome.full_ids,
                )
            )

    async def _deliver(self, batch: Batch, entry: ASPEntry, report: BatchReport) -> None:
        """Write results, fan out to merged callers, and cache each clean answer."""
        by_id = {r.request_id: r for r in batch.requests}
        fanned: list[Delivery] = []
        for delivery in report.deliveries:
            req = by_id.get(delivery.request_id)
            if req is None:
                continue
            # cache the individual answer for future identical requests (spec §5.5);
            # side-effectful modes never carry a fingerprint, and never cache
            if req.fingerprint and not delivery.partial_split and entry.mode in DEDUP_SAFE_MODES:
                await self.dedup.cache_result(
                    entry.asp_id, req.fingerprint, delivery.answer, entry.cache_ttl_seconds
                )
            # merged callers get the same answer, each charged (spec §3.3)
            for member in await self.dedup.merged_members(entry.asp_id, req.request_id):
                fanned.append(
                    Delivery(
                        request_id=member,
                        caller_id="",  # caller recorded at intake; log row already exists
                        answer=delivery.answer,
                        charged=delivery.charged,
                    )
                )
                report.dedup_hits += 1
            await self.dedup.clear_merge_set(entry.asp_id, req.request_id)
        if fanned:
            # each merged member is charged what the 402 gate collected from *them* —
            # a priority member paid the priority price even though it shared the slot
            ids = [d.request_id for d in fanned]
            async with self.engine.connect() as conn:
                prios = dict(
                    (
                        await conn.execute(
                            select(request_log.c.request_id, request_log.c.priority)
                            .where(request_log.c.request_id.in_(ids))
                        )
                    ).all()
                )
            for d in fanned:
                # a merged member shared the slot → definitionally batched → the discounted tier
                d.charged = settled_price(entry, entry.break_even_batch_size,
                                          priority=bool(prios.get(d.request_id)))
        report.deliveries.extend(fanned)

        async with self.engine.begin() as conn:
            for delivery in report.deliveries:
                await conn.execute(
                    update(request_log)
                    .where(request_log.c.request_id == delivery.request_id)
                    .values(
                        status=RequestStatus.DELIVERED.value,
                        result=delivery.answer,
                        charged=delivery.charged,
                        batch_id=report.batch_id,
                    )
                )

        # Record the inbound settlement (caller → TheHouse) at the ACTUAL tier price. This is the
        # SELLER direction: TheHouse settles the caller's signed x402 authorization (verified at the
        # gate) — NOT the a2a-pay rail, which is the BUYER direction (TheHouse → target). The row is
        # "settled" (the authorization is collected; prod backs it with the facilitator settle and
        # the deferred reconciler). Merged/fanned callers are attributed to their REAL caller_id.
        from thehouse.onchain.payments import SettlementLedger

        settlement = SettlementLedger(self.engine)
        # Resolve every caller in ONE query (not a connection per delivery). The billing ledger
        # (request_log) is the source of truth for who was charged.
        ids = [d.request_id for d in report.deliveries]
        caller_by_req = {r.request_id: r.caller_id for r in batch.requests}
        async with self.engine.connect() as conn:
            for rid, cid in (await conn.execute(
                    select(request_log.c.request_id, request_log.c.caller_id)
                    .where(request_log.c.request_id.in_(ids)))).all():
                caller_by_req.setdefault(rid, cid)
        for delivery in report.deliveries:
            counterparty = delivery.caller_id or caller_by_req.get(delivery.request_id)
            if not counterparty:
                # An unidentifiable delivery has no billing record — under concurrency a merged
                # member can lose its request_log row. NEVER book collected money to a phantom
                # (that is exactly the drift the reconciler would flag). Skip + warn instead.
                logger.warning("skipping settlement for unbilled/unknown request %s (no caller)",
                               delivery.request_id)
                continue
            await settlement.record(
                direction="in", counterparty=counterparty or "unknown",
                amount_usdt=delivery.charged, request_id=delivery.request_id,
                batch_id=report.batch_id, scheme="x402_settle")

    async def _ledger(self, batch: Batch, entry: ASPEntry, report: BatchReport) -> None:
        below = len(batch.requests) < entry.break_even_batch_size
        async with self.engine.begin() as conn:
            await conn.execute(
                insert(economics_ledger).values(
                    batch_id=report.batch_id,
                    asp_id=report.asp_id,
                    batch_size=len(batch.requests),
                    window_open_ms=batch.window_open_ms,
                    window_fire_reason=batch.fire_reason.value,
                    target_cost_paid=round(report.target_cost_paid, 6),
                    thehouse_revenue_collected=report.revenue,
                    gross_margin=report.gross_margin,
                    below_break_even=below,
                    dedup_hits=report.dedup_hits,
                    priority_surcharges=round(report.priority_surcharges, 6),
                    split_quality=report.split_quality.value,
                    created_at_ms=now_ms(),
                )
            )
        await audit(
            "batch_settled",
            {
                "batch_id": report.batch_id,
                "asp_id": report.asp_id,
                "size": len(batch.requests),
                "fire_reason": batch.fire_reason.value,
                "cost": report.target_cost_paid,
                "revenue": report.revenue,
                "margin": report.gross_margin,
                "split_quality": report.split_quality.value,
            },
            engine=self.engine,
        )


def _query_args(entry: ASPEntry, prompt: str) -> dict[str, str]:
    """Put the composed prompt into the tool's declared string parameter."""
    props = (entry.tool_schema.get("inputSchema") or entry.tool_schema).get("properties", {})
    for name, spec in props.items():
        if isinstance(spec, dict) and spec.get("type") == "string":
            return {name: prompt}
    return {"query": prompt}


def _to_json(value) -> str | None:
    if value is None:
        return None
    import json

    return json.dumps(value, separators=(",", ":"), default=str)
