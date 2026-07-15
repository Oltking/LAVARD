"""SPLITTER (spec §5.5).

Mode A — numeric anchor parsing: slice the response on the anchors TheHouse itself injected
(`1)`, `2)`, …) and map slice N to the request_id of query N. Deterministic: TheHouse planted
the anchors, TheHouse harvests them. No fuzzy NLP.

Mode B — key-based extraction: response is a JSON object; map response[parameter_value] →
request_id via the key map the Packer built.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from thehouse.core.models import SplitQuality

# An anchor is a line starting with `N)` or `N.` or `N:`.
_ANCHOR = re.compile(r"^[ \t]*(\d+)[\)\.:][ \t]*", re.MULTILINE)


@dataclass
class SplitOutcome:
    quality: SplitQuality
    answers: dict[str, str] = field(default_factory=dict)  # request_id -> answer
    partial_split: bool = False
    # request_ids whose answer is the FULL compound response (their own segment could not
    # be isolated). Only these see the other caller's material; their answers are never
    # cached. Callers whose segment parsed cleanly get just their segment.
    full_ids: set[str] = field(default_factory=set)


def split_numbered(text: str, order: list[str]) -> SplitOutcome:
    """Mode A. `order[i]` is the request_id whose answer should sit under anchor i+1."""
    if len(order) == 1:
        # Solo pass-through call: the whole response belongs to the one caller.
        return SplitOutcome(SplitQuality.CLEAN, {order[0]: text.strip()})

    matches = list(_ANCHOR.finditer(text))
    segments: dict[int, str] = {}
    for i, m in enumerate(matches):
        number = int(m.group(1))
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        if 1 <= number <= len(order) and number not in segments:
            segments[number] = text[start:end].strip()

    if len(segments) == len(order):
        return SplitOutcome(
            SplitQuality.CLEAN,
            {order[n - 1]: seg for n, seg in segments.items()},
        )

    if segments:
        # Malformed/incomplete anchors: callers whose segment parsed get exactly their
        # segment; only callers whose segment is missing receive the full response — at
        # the price already paid (no refund rail); the quality flag feeds auto-protection.
        answers = {order[n - 1]: seg for n, seg in segments.items()}
        missing = {rid for i, rid in enumerate(order) if (i + 1) not in segments}
        for rid in missing:
            answers[rid] = text.strip()
        return SplitOutcome(SplitQuality.PARTIAL, answers, partial_split=True, full_ids=missing)

    # No anchors parsed at all. No refund rail: the callers paid and the target answered,
    # so everyone receives the full response text (never cached) rather than nothing.
    # The FAILED flag still lands in the ledger and drives auto-protection.
    full = text.strip()
    return SplitOutcome(
        SplitQuality.FAILED,
        {rid: full for rid in order},
        partial_split=True,
        full_ids=set(order),
    )


def split_keyed(payload: dict[str, Any], key_map: dict[str, str]) -> SplitOutcome:
    """Mode B native. `key_map` maps parameter_value -> request_id (built by the Packer)."""
    answers: dict[str, str] = {}
    missing = []
    for key, request_id in key_map.items():
        if key in payload and payload[key] is not None:
            value = payload[key]
            answers[request_id] = value if isinstance(value, str) else _to_json(value)
        else:
            missing.append(key)

    if not answers:
        # Not one key matched. Same no-refund policy as Mode A: every caller receives the
        # full payload (never cached) rather than nothing; FAILED drives auto-protection.
        full = _to_json(payload)
        all_ids = set(key_map.values())
        return SplitOutcome(
            SplitQuality.FAILED,
            {rid: full for rid in all_ids},
            partial_split=True,
            full_ids=all_ids,
        )
    if missing:
        # a missing key still paid and gets the full payload (no refund rail) — marked
        # full so it is never cached as a per-question answer
        full = _to_json(payload)
        full_ids = {key_map[k] for k in missing}
        for rid in full_ids:
            answers[rid] = full
        return SplitOutcome(SplitQuality.PARTIAL, answers, partial_split=True, full_ids=full_ids)
    return SplitOutcome(SplitQuality.CLEAN, answers)


def _to_json(value: Any) -> str:
    import json

    return json.dumps(value, separators=(",", ":"), default=str)
