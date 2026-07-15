# TheHouse schema migrations (Alembic)

TheHouse's money-storing tables (`settlements`, `economics_ledger`, `request_log`, …) are
managed by Alembic so schema changes are ordered, reviewable, and safe on Postgres — not
implicit `create_all`.

The DB URL comes from `THEHOUSE_DATABASE_URL` (falls back to the dev sqlite file); `env.py`
drives the async engine, so the same history runs on sqlite (dev) and Postgres (prod).

```bash
# apply everything (dev sqlite)
PYTHONPATH=. alembic -c thehouse/alembic.ini upgrade head

# prod (Postgres)
THEHOUSE_DATABASE_URL=postgresql+asyncpg://user:pass@host/db \
  PYTHONPATH=. alembic -c thehouse/alembic.ini upgrade head

# after editing thehouse/core/storage/db.py, generate the next revision
PYTHONPATH=. alembic -c thehouse/alembic.ini revision --autogenerate -m "describe change"
```

`init_db()` (`create_all`) remains for the zero-setup dev/test path; **production must run
`alembic upgrade head`** and never rely on `create_all`.

> LAVARD's stdlib `core/store.py` is raw sqlite (offline core, no SQLAlchemy). Its schema is
> versioned by `PRAGMA user_version` with additive idempotent steps in `SqliteStore._migrate`.
