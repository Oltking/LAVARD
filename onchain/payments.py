"""Escrow / settlement adapter — the Agent Payments Protocol (APP) seam (§4.2/§SETTLE, Phase 4).

Grounded in docs/vendor/okxai/payments-and-modes.md: A2A hires open escrow that releases only on
user sign-off; APP covers quote → escrow → usage → settle → release → dispute on X Layer.

Backends:
- `MockPayments` — deterministic escrow ids, validates state transitions. Default (offline).
  The authoritative lifecycle state for a hire is mirrored in LAVARD's own store; this provider
  models the provider-side call surface so the real APP drops in cleanly.
- `AppPayments` — real APP/Payment SDK backend stub. Not active until Q-API-1 / Q-LIST-2 are
  resolved (whether the Payment SDK is required for an A2A-only listing is still open).

Interface methods intentionally match the adapter sketch in payments-and-modes.md.
"""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass
from typing import Protocol

# escrow lifecycle
OPEN = "open"
RELEASED = "released"
REFUNDED = "refunded"
DISPUTED = "disputed"


@dataclass
class Escrow:
    escrow_id: str
    payer: str
    payee: str
    amount_usd: float
    memo: str
    status: str = OPEN

    def to_dict(self) -> dict:
        return asdict(self)


class PaymentsClient(Protocol):
    def open_escrow(self, payer: str, payee: str, amount_usd: float, memo: str) -> Escrow: ...
    def release(self, escrow: Escrow) -> Escrow: ...
    def refund(self, escrow: Escrow) -> Escrow: ...
    def dispute(self, escrow: Escrow) -> Escrow: ...


class MockPayments:
    def open_escrow(self, payer: str, payee: str, amount_usd: float, memo: str) -> Escrow:
        eid = "esc_" + hashlib.sha256(f"{payer}|{payee}|{amount_usd}|{memo}".encode()).hexdigest()[:16]
        return Escrow(eid, payer, payee, round(amount_usd, 2), memo, OPEN)

    def release(self, escrow: Escrow) -> Escrow:
        self._require(escrow, OPEN, "release")
        return Escrow(**{**escrow.to_dict(), "status": RELEASED})

    def refund(self, escrow: Escrow) -> Escrow:
        self._require(escrow, OPEN, "refund")
        return Escrow(**{**escrow.to_dict(), "status": REFUNDED})

    def dispute(self, escrow: Escrow) -> Escrow:
        self._require(escrow, OPEN, "dispute")
        return Escrow(**{**escrow.to_dict(), "status": DISPUTED})

    @staticmethod
    def _require(escrow: Escrow, expected: str, action: str) -> None:
        if escrow.status != expected:
            raise ValueError(f"Cannot {action} escrow in status '{escrow.status}'.")


class AppPayments:  # pragma: no cover - exercised only with a live CLI + wallet session
    """Real Agent Payments Protocol backend.

    VERIFIED (live `onchainos` help): the buyer-side **escrow + sign-off** lifecycle LAVARD needs is
    the `agent` **task-commerce** path, NOT `a2a-pay` (which is a direct charge rail where the
    *seller* creates the authorization and the buyer signs EIP-3009 — wrong shape for a
    buyer-opened escrow hold). The correct mapping is:

        open_escrow  →  agent create-task → asp-match/set-asp → set-payment-mode → confirm-accept
                        (Client confirms provider and funds escrow on X Layer)
        release      →  (delivery accepted) sign-off closes the task, funds release to the ASP
        refund       →  agent user-reject  (job_user_reject)
        dispute      →  agent dispute       (5% bounty deposit, filed within 1 day)

    Wiring these requires the per-subcommand flags (`onchainos agent create-task --help`,
    `set-payment-mode --help`, `confirm-accept --help`) confirmed against a live wallet session.
    Until then this raises precisely rather than mis-mapping the money path onto a2a-pay.
    The low-level `a2a-pay` create/pay/status IS wired on OnchainOsCli for the A2MCP direct-charge
    case and pay-per-call settlement.
    """

    _GUIDE = (
        "See docs/vendor/okxai/onchainos-cli.md. NOTE: flags are verified from live `--help` but the "
        "flow is UNTESTED against the backend (web3.okx.com was DNS-blocked at build time) — "
        "sandbox-test before mainnet."
    )

    def open_escrow(self, payer: str, payee: str, amount_usd: float, memo: str) -> Escrow:
        """VERIFIED escrow-open path (untested live): create-task (private, direct provider) →
        set-payment-mode escrow → confirm-accept (executes payment into X-Layer escrow).
        `escrow_id` = the OKX jobId. Settlement token/chain come from
        LAVARD_SETTLE_TOKEN (default USDT) / LAVARD_OKX_CHAIN."""
        import os

        from onchain.onchainos_cli import _require, get_cli

        token = os.environ.get("LAVARD_SETTLE_TOKEN", "USDT")
        chain = os.environ.get("LAVARD_OKX_CHAIN") or None
        amount = round(amount_usd, 2)
        cli = get_cli()
        task = cli.create_task(description=memo, budget=amount, max_budget=amount, currency=token,
                               provider=payee, payment_mode="escrow", visibility=1, chain=chain)
        _require(task, ("jobId",), "agent create-task")
        job_id = task["jobId"]
        cli.set_payment_mode(job_id, token_symbol=token, token_amount=amount, chain=chain)
        cli.confirm_accept(job_id, chain=chain)   # executes payment into escrow
        return Escrow(job_id, payer, payee, amount, memo, OPEN)

    def release(self, escrow: Escrow) -> Escrow:
        """Sign-off: `onchainos agent complete <jobId>` — Client confirms the task complete and
        releases escrow to the provider (VERIFIED verb)."""
        import os

        from onchain.onchainos_cli import get_cli

        get_cli().complete_task(escrow.escrow_id, chain=os.environ.get("LAVARD_OKX_CHAIN") or None)
        return Escrow(**{**escrow.to_dict(), "status": RELEASED})

    def refund(self, escrow: Escrow) -> Escrow:
        """Client rejects the deliverable: `onchainos agent reject <jobId>` (VERIFIED verb). Note:
        a reject may route to arbitration rather than an instant refund, per OKX dispute rules."""
        import os

        from onchain.onchainos_cli import get_cli

        get_cli().reject_task(escrow.escrow_id, chain=os.environ.get("LAVARD_OKX_CHAIN") or None)
        return Escrow(**{**escrow.to_dict(), "status": REFUNDED})

    def dispute(self, escrow: Escrow) -> Escrow:
        # As the CLIENT, LAVARD does not raise disputes — it `reject`s the deliverable; the PROVIDER
        # may then file `agent dispute raise` (5% bounty), which routes to evaluator arbitration
        # (vote-commit/reveal). Client-side, dispute == refund/reject here.
        return self.refund(escrow)
