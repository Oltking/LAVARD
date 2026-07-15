# LAVARD persistence — production stance

## Current: stdlib sqlite (WAL), single-node

LAVARD's core store (`core/store.py`) is dependency-free sqlite in WAL mode with a busy timeout
and a `PRAGMA user_version` migration guard. This is **acceptable for single-node production** at
low-to-moderate volume — WAL gives concurrent readers + one writer, and the money/audit tables
(hash-chained, HMAC-sealed) are durable on a single host.

`Settings.validate_for_prod()` does **not** block a sqlite prod boot; `prod_warnings()` emits a
non-fatal advisory so the operator is reminded of the single-node limit.

## Deferred: Postgres backend for HA (multi-replica)

A rushed Postgres port of the rich store API (hash-chained audit, HMAC head seal, checkpointing,
reputation, insights) is riskier to money records than staying on proven sqlite, so it is
**formally deferred** rather than half-built. When horizontal scale / HA is required:

1. Implement `PostgresJobStore` behind `get_store()` (the `postgresql+...` branch that currently
   raises `NotImplementedError`), mirroring `SqliteJobStore`'s method surface exactly. The
   SQLAlchemy schema already exists in `core/db.py` / `core/models.py`.
2. Manage schema with **Alembic** (already wired for the bundled `thehouse` package —
   `thehouse/migrations/`); add a LAVARD Alembic env pointed at `core/models.py` metadata.
3. Port the audit-chain writes inside a single transaction (the seal + row must be atomic).
4. Load-test concurrent writers before cutover; migrate existing sqlite data with a one-shot ETL.

Until then: run one LAVARD instance per store, or shard by owner. TheHouse (the aggregator) already
supports Postgres via `THEHOUSE_DATABASE_URL` + Alembic, so the money-batching layer scales
independently of LAVARD's orchestration store.
