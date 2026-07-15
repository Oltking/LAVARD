"""Structured-ish logging for the app process: one line per event, key=value fields.

Called once from the API lifespan; safe to call twice (basicConfig is a no-op if the
root logger is already configured)."""

from __future__ import annotations

import logging

FORMAT = '%(asctime)s level=%(levelname)s logger=%(name)s msg="%(message)s"'


def setup_logging(level: int = logging.INFO) -> None:
    logging.basicConfig(level=level, format=FORMAT)
    # uvicorn configures its own handlers; keep our app loggers from double-printing
    logging.getLogger("thehouse").propagate = True
