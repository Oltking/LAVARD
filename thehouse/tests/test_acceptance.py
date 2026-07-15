"""Phase 12: the scripted acceptance run (spec §10) passes end to end."""

from thehouse.scripts.acceptance_run import run


async def test_acceptance_run(tmp_path):
    await run(f"sqlite+aiosqlite:///{tmp_path}/acceptance.db")
