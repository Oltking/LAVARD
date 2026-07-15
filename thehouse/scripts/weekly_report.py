"""Render the weekly economics report and run the nightly auto-protection pass.

Run: python -m scripts.weekly_report
"""

import asyncio

from thehouse.core.economics.service import EconomicsEngine
from thehouse.core.storage.db import get_engine, init_db


async def main() -> None:
    await init_db()
    engine = EconomicsEngine(get_engine())

    report = await engine.weekly_report()
    print(report.render())

    actions = await engine.run_auto_protection()
    print("\nAuto-protection actions:")
    if not actions:
        print("  none")
    for a in actions:
        print(f"  {a.asp_id}: {a.action} — {a.detail}")


if __name__ == "__main__":
    asyncio.run(main())
