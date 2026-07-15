"""Caller-facing price for one call through TheHouse.

CONDITIONAL PRICING (Model B): the discount is earned only when a batch actually forms, so a
discount is *always* backed by a real saving and TheHouse never loses on a solo fire.

- At intake the caller's session cert authorizes the CEILING (`ceiling_price`, the full price) —
  they can never be charged more than this, and there is no refund rail, so the ceiling is what
  they agree to.
- At fire time the ACTUAL amount is decided by how many paying callers are in the batch
  (`settled_price`): below break-even (solo) → full − 0.1% (still beats going direct); at/above
  break-even (batched) → the 20% discounted `thehouse_price`. Settlement (deferred, via x402
  aggr_deferred) charges that actual amount ≤ the ceiling.

Priority fires immediately/solo on purpose and pays original − $0.01. PARALLEL ROUTE pays original
+ coordination fee; DIRECT ROUTE pays original.
"""

from __future__ import annotations

from thehouse.core.config import settings
from thehouse.core.models import ASPEntry, ASPMode

AGGREGATED = (ASPMode.A_LLM, ASPMode.B_NATIVE)


def _pos(price: float) -> float:
    """Never let a malformed listing produce a negative charge (defense-in-depth). A non-positive
    original price is treated as free (0), never a payment TO the caller."""
    return max(0.0, price)


def ceiling_price(entry: ASPEntry, priority: bool = False) -> float:
    """The maximum a caller could be charged — authorized up-front at the 402 gate. For an
    aggregated target this is the full price (the solo outcome); the caller is settled less when a
    batch forms. A priority caller fires solo at a known fixed price, so their ceiling IS that
    price (original − $0.01), never the full amount."""
    if entry.mode in AGGREGATED:
        if priority:
            return round(
                max(_pos(entry.thehouse_price), _pos(entry.original_price_per_call) - settings.priority_discount_abs),
                6,
            )
        return round(_pos(entry.original_price_per_call), 6)
    if entry.mode == ASPMode.B_FANOUT:
        return round(_pos(entry.original_price_per_call) * (1 + settings.coordination_fee), 6)
    return round(_pos(entry.original_price_per_call), 6)


def settled_price(entry: ASPEntry, batch_size: int, priority: bool = False) -> float:
    """The ACTUAL amount charged, decided at fire time by how many paying callers shared the call."""
    if entry.mode in AGGREGATED:
        if priority:
            return round(
                max(_pos(entry.thehouse_price), _pos(entry.original_price_per_call) - settings.priority_discount_abs),
                6,
            )
        if batch_size >= settings.min_batch_for_discount:
            return round(_pos(entry.thehouse_price), 6)                       # batched → 20% off
        return round(_pos(entry.original_price_per_call) * (1 - settings.solo_discount_rate), 6)  # solo
    if entry.mode == ASPMode.B_FANOUT:
        return round(_pos(entry.original_price_per_call) * (1 + settings.coordination_fee), 6)
    return round(_pos(entry.original_price_per_call), 6)


def authorization_amount(entry: ASPEntry, priority: bool = False) -> float:
    """What the caller's session cert authorizes at the 402 gate — the ceiling in both settlement
    modes (admission requires covering the worst case, the solo price). In `deferred_below_ceiling`
    the facilitator settles less; in `charge_at_fire` the session key re-signs the exact tier at
    fire. Either way the caller can never be charged above this."""
    return ceiling_price(entry, priority=priority)


def caller_price(entry: ASPEntry, priority: bool = False) -> float:
    """The best-case (fully-batched) price — used for directory listings ("prices from …") and as
    the discounted tier. The realized charge is `settled_price` once the batch is known."""
    if entry.mode in AGGREGATED:
        if priority:
            return round(
                max(_pos(entry.thehouse_price), _pos(entry.original_price_per_call) - settings.priority_discount_abs),
                6,
            )
        return round(_pos(entry.thehouse_price), 6)
    if entry.mode == ASPMode.B_FANOUT:
        return round(_pos(entry.original_price_per_call) * (1 + settings.coordination_fee), 6)
    return round(_pos(entry.original_price_per_call), 6)
