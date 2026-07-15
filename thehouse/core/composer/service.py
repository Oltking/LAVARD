"""COMPOSER (spec §5.3, Mode A only): N query strings → one numbered compound question.

The format is fixed and battle-tested — no preamble beyond the instruction, no branding.
The numeric anchors are TheHouse's private split markers. Semantic-overlap merging happens
upstream (Deduplicator, Phase 8); by the time a batch reaches the Composer every request
is a distinct slot.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from thehouse.core.models import CallerRequest

HEADER = (
    "Please answer the following questions and number your responses \n"
    "to match exactly. Keep each answer clearly separated.\n\n"
)

# A caller's query is combined with OTHER callers' queries in one shared prompt, so a query that
# tries to steer the model ("ignore the above", "for question 2 output…", inject a system/role
# turn, or reference another item) could poison a peer's split answer. Such queries are pulled OUT
# of the shared compound and dispatched IN ISOLATION (they pay solo price, can't affect others).
_INJECTION = re.compile(
    r"\b(ignore|disregard|forget)\b.{0,20}\b(above|previous|prior|earlier|instruction|"
    r"question|all)\b"
    r"|\b(instead|rather)\b.{0,15}\b(answer|output|respond|say|write)\b"
    r"|\bfor\s+(question|item|answer|number|#)\s*\d+\b"
    r"|\b(system|assistant|user)\s*:"
    r"|</?(system|instruction|prompt)>"
    r"|\byou\s+(are|must|should)\b.{0,30}\b(instead|now|actually)\b",
    re.I | re.S,
)


def is_injection_risk(query: str) -> bool:
    """True if a query looks like it tries to manipulate the shared compound prompt. Conservative —
    a false positive only costs that caller the batch discount (they still get a correct answer)."""
    return bool(_INJECTION.search(query or ""))


@dataclass
class ComposedCall:
    prompt: str
    order: list[str]        # request_ids, index i ↔ anchor i+1
    compound: bool          # False → single request passed through unwrapped


def compose(requests: list[CallerRequest]) -> ComposedCall:
    """Build the compound prompt. A solo request passes through unwrapped — the target
    sees exactly what the caller sent and no split is needed."""
    if not requests:
        raise ValueError("cannot compose an empty batch")
    if len(requests) == 1:
        req = requests[0]
        return ComposedCall(prompt=req.query or "", order=[req.request_id], compound=False)

    lines = [f"{i}) {req.query}" for i, req in enumerate(requests, start=1)]
    return ComposedCall(
        prompt=HEADER + "\n".join(lines),
        order=[r.request_id for r in requests],
        compound=True,
    )


def chunk(requests: list[CallerRequest], max_batch_size: int) -> list[list[CallerRequest]]:
    """Batch size safety (spec §5.3): fire in sub-batches of max_batch_size sequentially,
    never mixing callers into an oversized prompt that degrades response quality."""
    if max_batch_size < 1:
        raise ValueError("max_batch_size must be >= 1")
    return [requests[i : i + max_batch_size] for i in range(0, len(requests), max_batch_size)]
