"""Price re-sync — keeps TheHouse's prices tied to the targets' registered fees.

Targets register fees on-chain (fixed USDT per service). When a target changes its fee,
two things must follow automatically:

1. **Registry side** (this process): `asp_registry.original_price_per_call` takes the new
   fee and `thehouse_price` is re-derived as fee × discount_rate, so the 402 gate, the
   directory, and the economics ledger all quote the new price on the next request.
2. **Listing side** (OKX.AI): TheHouse's own service entry for that target carries a fixed
   on-chain fee too, so it must be pushed via `onchainos agent update`.

Both faces are pluggable, mirroring the payments module:
- `StaticFeeSource` / `RecordingListingUpdater` — dev profile, no chain, fully testable.
- `OnchainOSFeeSource` / `OnchainOSListingUpdater` — prod, shells out to the `onchainos`
  CLI (same pattern as `OnchainOSSigner`).

Every applied change is written to the audit log. Run `sync_once()` from a cron or call
`start(interval_s)` for a background loop alongside the aggregator sweeper.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncEngine

from thehouse.core.config import settings
from thehouse.core.models import ASPEntry
from thehouse.core.profiler.registry import RegistryService
from thehouse.core.storage.db import audit


@dataclass
class PriceChange:
    asp_id: str
    old_fee: float
    new_fee: float
    old_thehouse_price: float
    new_thehouse_price: float


class FeeSource(Protocol):
    async def fetch_fees(self) -> dict[str, float]:
        """asp_id → the target's currently registered fee in USDT. Absent asp_ids are
        left untouched (a partial read is not a delisting signal)."""


class ListingUpdater(Protocol):
    async def update_fee(self, entry: ASPEntry, new_thehouse_fee: float) -> None:
        """Push TheHouse's new fee for this target's resale service to the OKX listing."""


class StaticFeeSource:
    """Dev profile: fees come from a plain dict (or anything you mutate between syncs)."""

    def __init__(self, fees: dict[str, float] | None = None):
        self.fees: dict[str, float] = dict(fees or {})

    async def fetch_fees(self) -> dict[str, float]:
        return dict(self.fees)


class RecordingListingUpdater:
    """Dev profile: records what would be pushed to OKX so tests and dry runs can assert."""

    def __init__(self) -> None:
        self.pushed: list[tuple[str, float]] = []

    async def update_fee(self, entry: ASPEntry, new_thehouse_fee: float) -> None:
        self.pushed.append((entry.asp_id, new_thehouse_fee))


class OnchainOSFeeSource:
    """Prod: read each target's registered fee via `onchainos agent service-list`.

    `agent_ids` maps our asp_id → the target's OKX agent id (the same ids used to
    populate the registry at onboarding). The service row is matched by tool/service
    name when possible, else the first service is taken.
    """

    def __init__(self, agent_ids: dict[str, str], tool_names: dict[str, str] | None = None):
        self.agent_ids = dict(agent_ids)
        self.tool_names = dict(tool_names or {})

    async def fetch_fees(self) -> dict[str, float]:
        fees: dict[str, float] = {}
        for asp_id, agent_id in self.agent_ids.items():
            services = await self._service_list(agent_id)
            row = self._match(asp_id, services)
            if row is not None:
                try:
                    fees[asp_id] = float(str(row.get("fee", "")).replace("USDT", "").strip())
                except ValueError:
                    continue  # unparsable fee: skip rather than corrupt the registry
        return fees

    def _match(self, asp_id: str, services: list[dict]) -> dict | None:
        wanted = self.tool_names.get(asp_id)
        if wanted:
            for s in services:
                if s.get("name") == wanted:
                    return s
        return services[0] if services else None

    async def _service_list(self, agent_id: str) -> list[dict]:
        proc = await asyncio.create_subprocess_exec(
            "onchainos", "agent", "service-list", "--agent-id", str(agent_id), "--json",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        out, err = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(f"onchainos agent service-list failed: {err.decode()[:500]}")
        data = json.loads(out)
        return data.get("services", data) if isinstance(data, dict) else data


class OnchainOSListingUpdater:
    """Prod: push TheHouse's updated resale fee via `onchainos agent update`.

    `agent update` replaces the service set wholesale (PLATFORM_ASP_LISTING.md §6), so
    the full current service list is rebuilt from the registry on every push.
    """

    def __init__(self, engine: AsyncEngine, thehouse_agent_id: str, endpoint: str):
        self.engine = engine
        self.thehouse_agent_id = thehouse_agent_id
        self.endpoint = endpoint

    async def update_fee(self, entry: ASPEntry, new_thehouse_fee: float) -> None:
        from thehouse.core.pricing import caller_price

        services = [
            {
                "name": e.tool_name,
                "description": e.description or f"{e.asp_id} via TheHouse",
                "type": "A2MCP",
                "fee": f"{caller_price(e):.6f}".rstrip("0").rstrip("."),
                "endpoint": self.endpoint,
            }
            for e in await RegistryService(self.engine).list_all(active_only=True)
        ]
        proc = await asyncio.create_subprocess_exec(
            "onchainos", "agent", "update", "--agent-id", str(self.thehouse_agent_id),
            "--services", json.dumps(services),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        _, err = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(f"onchainos agent update failed: {err.decode()[:500]}")


class PriceSyncService:
    """Compare the fee source against the registry and apply + propagate every change."""

    def __init__(
        self,
        engine: AsyncEngine,
        source: FeeSource,
        updater: ListingUpdater | None = None,
    ):
        self.engine = engine
        self.registry = RegistryService(engine)
        self.source = source
        self.updater = updater
        self._task: asyncio.Task | None = None
        self.last_changes: list[PriceChange] = []

    async def sync_once(self) -> list[PriceChange]:
        fees = await self.source.fetch_fees()
        changes: list[PriceChange] = []
        for entry in await self.registry.list_all():
            new_fee = fees.get(entry.asp_id)
            if new_fee is None or abs(new_fee - entry.original_price_per_call) < 1e-9:
                continue
            change = PriceChange(
                asp_id=entry.asp_id,
                old_fee=entry.original_price_per_call,
                new_fee=new_fee,
                old_thehouse_price=entry.thehouse_price,
                new_thehouse_price=round(new_fee * settings.discount_rate, 6),
            )
            entry.original_price_per_call = new_fee
            entry.thehouse_price = change.new_thehouse_price
            await self.registry.upsert(entry)
            await audit("price_sync", change.__dict__, engine=self.engine)
            if self.updater is not None:
                await self.updater.update_fee(entry, change.new_thehouse_price)
            changes.append(change)
        self.last_changes = changes
        return changes

    def start(self, interval_s: float = 3600.0) -> None:
        async def loop() -> None:
            while True:
                try:
                    await self.sync_once()
                except Exception as exc:  # a failed sync must never kill the loop
                    await audit("price_sync_error", {"error": str(exc)}, engine=self.engine)
                await asyncio.sleep(interval_s)

        self._task = asyncio.get_event_loop().create_task(loop())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
