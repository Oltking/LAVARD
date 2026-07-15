"""Test setup: point the DB at a throwaway SQLite file BEFORE any core module imports.

`core.db` builds its engine at import time from settings, so the env var must be set here (pytest
imports conftest before collecting tests). Model backend stays unconfigured, so the deterministic
heuristic planner runs — tests are fully offline and reproducible.
"""

from __future__ import annotations

import os
import tempfile

_tmp = tempfile.NamedTemporaryFile(prefix="lavard_test_", suffix=".db", delete=False)
_tmp.close()
os.environ["LAVARD_DATABASE_URL"] = f"sqlite:///{_tmp.name}"

# Portable Memory is a separate, persistent store — isolate it too so tests never touch the
# real ./lavard_memory.db. Owner-scoping keeps memory tests from leaking into other tests.
_mem = tempfile.NamedTemporaryFile(prefix="lavard_test_mem_", suffix=".db", delete=False)
_mem.close()
os.environ["LAVARD_MEMORY_URL"] = f"sqlite:///{_mem.name}"

os.environ.pop("LAVARD_MODEL_ENDPOINT", None)
os.environ.pop("LAVARD_MODEL_API_KEY", None)

# TheHouse (bundled aggregator) runs in its dev profile for tests: in-memory-ish sqlite + fakeredis,
# so the API lifespan wires a real TheHouseExecutor without any external services.
os.environ.setdefault("THEHOUSE_PROFILE", "dev")
os.environ.setdefault(
    "THEHOUSE_DATABASE_URL", f"sqlite+aiosqlite:///{tempfile.mkdtemp()}/thehouse-test.db"
)
