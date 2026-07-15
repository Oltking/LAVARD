"""Observability — structured logging + a pluggable alert sink for money/audit signals.

Production needs to *know* when something financial or integrity-related happens: an escrow
release, a budget halt, a failed settlement, ledger drift, or an audit-chain verification failure.
`alert()` emits a structured event to the configured sink; the default sink logs at WARNING (so it
shows up), and a deployment can install a webhook/pager sink via `set_alert_sink`.

Kept stdlib-only so LAVARD's offline core stays dependency-free.
"""

from __future__ import annotations

import json
import logging
from typing import Callable

logger = logging.getLogger("lavard")

# Severity ranking for sinks that filter.
SEVERITIES = ("info", "notice", "warning", "critical")

# Events that must always be surfaced (financial + integrity). Not exhaustive — any event name
# is accepted; these are documented so operators know what to alert on.
MONEY_AND_INTEGRITY_EVENTS = frozenset({
    "escrow_released", "budget_halt", "settlement_failed", "settlement_drift",
    "audit_verification_failed", "spend_escalated",
})

AlertSink = Callable[[dict], None]


def _default_sink(event: dict) -> None:
    sev = event.get("severity", "warning")
    level = {"info": logging.INFO, "notice": logging.INFO,
             "warning": logging.WARNING, "critical": logging.ERROR}.get(sev, logging.WARNING)
    logger.log(level, "ALERT %s", json.dumps(event, sort_keys=True, default=str))


_sink: AlertSink = _default_sink


def set_alert_sink(sink: AlertSink) -> None:
    """Install a custom alert sink (e.g. Slack/pager webhook). Pass None to reset to the default."""
    global _sink
    _sink = sink or _default_sink


def alert(event: str, severity: str = "warning", **fields) -> dict:
    """Emit a structured alert. Returns the event dict (handy for tests/audit correlation)."""
    payload = {"event": event, "severity": severity, **fields}
    try:
        _sink(payload)
    except Exception:  # a broken sink must never break the money path
        logger.exception("alert sink failed for event %s", event)
    return payload
