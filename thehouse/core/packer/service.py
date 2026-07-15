"""PACKER (spec §3.2, Mode B): native multi-parameter packing and fan-out planning."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from thehouse.core.models import ASPEntry, CallerRequest


@dataclass
class PackedCall:
    arguments: dict[str, Any]      # e.g. {"symbols": ["BTC", "ETH", "SOL"]}
    key_map: dict[str, str]        # parameter value -> request_id (Splitter contract)


def scalar_value(entry: ASPEntry, req: CallerRequest) -> str:
    """The per-request scalar the batch array is built from (e.g. symbol="BTC")."""
    if entry.batch_param:
        singular = entry.batch_param.rstrip("s")
        if singular in req.arguments:
            return str(req.arguments[singular])
    for value in req.arguments.values():
        if isinstance(value, (str, int, float)):
            return str(value)
    raise ValueError(f"request {req.request_id} carries no scalar parameter to pack")


def pack(entry: ASPEntry, requests: list[CallerRequest]) -> PackedCall:
    """Pack N scalar requests into one array-parameter call (B_native)."""
    if not entry.batch_param:
        raise ValueError(f"{entry.asp_id} has no batch_param — cannot native-pack")
    key_map: dict[str, str] = {}
    values: list[str] = []
    for req in requests:
        value = scalar_value(entry, req)
        if value not in key_map:  # identical values share one slot; Splitter fans out
            values.append(value)
        key_map[value] = key_map.get(value, req.request_id)
    return PackedCall(arguments={entry.batch_param: values}, key_map=key_map)


def duplicate_map(entry: ASPEntry, requests: list[CallerRequest]) -> dict[str, list[str]]:
    """request_ids that packed onto the same value as an earlier request → served the
    same keyed answer."""
    seen: dict[str, str] = {}
    dupes: dict[str, list[str]] = {}
    for req in requests:
        value = scalar_value(entry, req)
        if value in seen:
            dupes.setdefault(seen[value], []).append(req.request_id)
        else:
            seen[value] = req.request_id
    return dupes
