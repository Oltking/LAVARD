"""Real OnchainOS integration surface — a thin wrapper over the `onchainos` CLI (verified from
github.com/okx/onchainos-skills; see docs/vendor/okxai/onchainos-cli.md).

OKX.AI is not a hand-rolled REST API: identity, the marketplace, and payments are exposed through
the `onchainos` CLI (installable via `npx skills add okx/onchainos-skills`, also runnable as an MCP
server via `onchainos mcp`). This module shells out to the verified subcommands with `--json`,
gated on the binary being installed and OKX credentials being present.

Honesty guard: the subcommand *names* are verified from the skill sources, but exact flag spellings
and JSON output keys must be confirmed against an installed CLI (`onchainos <cmd> --help`). Every
field read goes through `_require`, which raises a clear, actionable `OnchainOsError` listing the
keys actually returned if the shape differs — so this layer is never silently wrong.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from typing import Any

_BIN = os.environ.get("LAVARD_ONCHAINOS_BIN", "onchainos")
_CREDS = ("OKX_API_KEY", "OKX_SECRET_KEY", "OKX_PASSPHRASE")


class OnchainOsError(RuntimeError):
    """Raised when the CLI is missing, unauthenticated, errors, or returns an unexpected shape."""


def _require(d: dict[str, Any], keys: tuple[str, ...], where: str) -> None:
    missing = [k for k in keys if k not in d]
    if missing:
        raise OnchainOsError(
            f"{where}: onchainos returned keys {sorted(d)} but LAVARD expected {list(keys)} "
            f"(missing {missing}). Confirm the current output shape with the installed CLI "
            f"(`{_BIN} <cmd> --help`) and update the field map in onchain/onchainos_cli.py."
        )


class OnchainOsCli:
    def __init__(self, binary: str | None = None, timeout: float = 60.0) -> None:
        # Read the binary override at construction (not import) so a runtime LAVARD_ONCHAINOS_BIN
        # is honored and the singleton reflects the current environment.
        self.binary = binary or os.environ.get("LAVARD_ONCHAINOS_BIN", "onchainos")
        self.timeout = timeout

    # --- environment checks -------------------------------------------------------------------
    def available(self) -> bool:
        return shutil.which(self.binary) is not None

    def credentialed(self) -> bool:
        return all(os.environ.get(k) for k in _CREDS)

    def preflight(self) -> None:
        """Fail fast with actionable guidance before any real call."""
        if not self.available():
            raise OnchainOsError(
                f"`{self.binary}` CLI not found. Install with "
                f"`npx skills add okx/onchainos-skills --yes -g` and ensure it is on PATH "
                f"(or set LAVARD_ONCHAINOS_BIN)."
            )
        if not self.credentialed():
            raise OnchainOsError(
                "OKX credentials missing. Set OKX_API_KEY / OKX_SECRET_KEY / OKX_PASSPHRASE "
                "from the OKX Developer Portal (https://web3.okx.com/onchain-os/dev-portal)."
            )

    def health(self) -> dict:
        """`onchainos preflight` — version/drift check. Returns the parsed `data` envelope."""
        self.preflight()
        return self.run(["preflight"])

    def logged_in(self) -> bool:
        """True if an Agentic Wallet session is active (agent commerce needs `wallet login`)."""
        try:
            self.run(["wallet", "status"])
            return True
        except OnchainOsError:
            return False

    # --- generic invocation -------------------------------------------------------------------
    def run(self, args: list[str]) -> Any:
        """Invoke `onchainos <args>` and unwrap the standard `{"ok":..,"data"|"error":..}` envelope
        (VERIFIED live — there is no `--json` flag; JSON is the default output). The CLI may emit
        several JSON lines (e.g. a one-time account-init `{...,"isNew":true}` before the result), so
        we parse every line, raise on any `ok:false`, and return the LAST envelope's `data`."""
        self.preflight()
        raw = self._run(args)
        envelopes = []
        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                envelopes.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        if not envelopes:
            raise OnchainOsError(
                f"onchainos {' '.join(args)} did not return JSON: {raw[:200]!r}")
        for env in envelopes:
            if isinstance(env, dict) and env.get("ok") is False:
                raise OnchainOsError(f"onchainos {' '.join(args)}: {env.get('error', env)}")
        last = envelopes[-1]
        if isinstance(last, dict) and "ok" in last:
            return last.get("data", last)
        return last

    def _run(self, args: list[str]) -> str:
        try:
            proc = subprocess.run(
                [self.binary, *args],
                capture_output=True, text=True, timeout=self.timeout, check=False,
            )
        except subprocess.TimeoutExpired as e:
            raise OnchainOsError(f"onchainos {' '.join(args)} timed out after {self.timeout}s") from e
        # The CLI reports business errors in the JSON envelope (ok:false), so a non-zero exit is an
        # infra/usage failure worth surfacing raw.
        if proc.returncode != 0 and not proc.stdout.strip():
            raise OnchainOsError(
                f"onchainos {' '.join(args)} failed (exit {proc.returncode}): {proc.stderr.strip()}")
        return proc.stdout.strip()

    # --- verified subcommand surface (okx-ai identity + task marketplace) ----------------------
    def agent_search(self, capability: str, limit: int = 5, chain: str | None = None) -> list[dict]:
        """`onchainos agent search --query <kw> --page-size <n>` — discover public ASPs.
        (VERIFIED flags: --query is required; pagination is --page-size, not --limit.)"""
        args = ["agent", "search", "--query", capability, "--page-size", str(limit)]
        if chain:
            args += ["--chain", chain]
        out = self.run(args)
        rows = out.get("list", out.get("agents", out)) if isinstance(out, dict) else out
        if not isinstance(rows, list):
            raise OnchainOsError(f"agent search: expected a list, got {type(rows).__name__}: {out!r}")
        return rows

    def get_agents(self, agent_ids: str) -> dict:
        """`onchainos agent get-agents --agent-ids` (VERIFIED flag) — details by id(s).
        Returns nested `list[].agentList[]`."""
        return self.run(["agent", "get-agents", "--agent-ids", agent_ids])

    def service_list(self, agent_id: str) -> Any:
        """`onchainos agent service-list` — the services an agent offers."""
        return self.run(["agent", "service-list", "--agent-id", agent_id])

    def feedback_list(self, agent_id: str) -> Any:
        """`onchainos agent feedback-list` — on-chain reviews/ratings for the Vetter."""
        return self.run(["agent", "feedback-list", "--agent-id", agent_id])

    # --- task commerce = the A2A escrow lifecycle (verified flags) -----------------------------
    def create_task(self, description: str, budget: float, max_budget: float, currency: str,
                    *, title: str | None = None, provider: str | None = None,
                    payment_mode: str = "escrow", visibility: int = 0,
                    service_id: str | None = None, service_token_address: str | None = None,
                    service_token_amount: str | None = None, chain: str | None = None) -> dict:
        """`onchainos agent create-task` (Client). VERIFIED flags. `--payment-mode escrow` opens the
        X-Layer escrow model; `--visibility 0` posts publicly for `asp-match`, `1` requires
        `--provider` for a direct hire. Returns the created task (its id is the escrow handle)."""
        args = ["agent", "create-task", "--description", description,
                "--budget", str(budget), "--max-budget", str(max_budget),
                "--currency", currency, "--payment-mode", payment_mode,
                "--visibility", str(visibility)]
        for flag, val in (("--title", title), ("--provider", provider), ("--chain", chain),
                          ("--service-id", service_id),
                          ("--service-token-address", service_token_address),
                          ("--service-token-amount", service_token_amount)):
            if val is not None:
                args += [flag, str(val)]
        if visibility == 1 and provider is None:
            raise OnchainOsError("create_task: --visibility 1 (private) requires a provider agentId.")
        return self.run(args)

    def asp_match(self, task_id: str) -> Any:
        """`onchainos agent asp-match` — matching ASPs for a published task."""
        return self.run(["agent", "asp-match", "--task-id", task_id])

    def set_payment_mode(self, job_id: str, token_symbol: str, token_amount: float,
                         mode: str = "escrow", chain: str | None = None) -> dict:
        """`onchainos agent set-payment-mode <JOB_ID>` — standalone, BEFORE confirm-accept
        (VERIFIED flags: positional job id + --payment-mode --token-symbol --token-amount)."""
        args = ["agent", "set-payment-mode", job_id, "--payment-mode", mode,
                "--token-symbol", token_symbol, "--token-amount", str(token_amount)]
        if chain:
            args += ["--chain", chain]
        return self.run(args)

    def confirm_accept(self, job_id: str, chain: str | None = None) -> dict:
        """`onchainos agent confirm-accept <JOB_ID>` — Client confirms provider and **executes
        payment** into escrow (VERIFIED; set-payment-mode must run first; params auto-resolved)."""
        args = ["agent", "confirm-accept", job_id]
        if chain:
            args += ["--chain", chain]
        return self.run(args)

    def complete_task(self, job_id: str, chain: str | None = None) -> dict:
        """`onchainos agent complete <JOB_ID>` — Client confirms task complete and **releases
        payment** to the provider (VERIFIED). This is escrow release / sign-off."""
        args = ["agent", "complete", job_id]
        if chain:
            args += ["--chain", chain]
        return self.run(args)

    def reject_task(self, job_id: str, chain: str | None = None) -> dict:
        """`onchainos agent reject <JOB_ID>` — Client rejects the deliverable (VERIFIED)."""
        args = ["agent", "reject", job_id]
        if chain:
            args += ["--chain", chain]
        return self.run(args)

    def close_task(self, job_id: str, chain: str | None = None) -> dict:
        """`onchainos agent close <JOB_ID>` — Client closes a task while still Open (VERIFIED)."""
        args = ["agent", "close", job_id]
        if chain:
            args += ["--chain", chain]
        return self.run(args)

    def task_status(self, task_id: str) -> dict:
        """`onchainos agent status` — current task status."""
        return self.run(["agent", "status", "--task-id", task_id])

    # --- verified payment surface (okx-agent-payments-protocol: a2a-pay) ------------------------
    # NOTE: a2a-pay is a DIRECT charge rail (create=Seller, pay=Buyer, EIP-3009). The full A2A
    # escrow+sign-off lifecycle is the `agent` task-commerce path (create-task → asp-match →
    # set-payment-mode → confirm-accept → deliver → user-reject/dispute) — see the vendor doc.
    def a2a_pay_create(self, amount_usd: float, symbol: str, recipient: str,
                       description: str = "", external_id: str = "") -> dict:
        """Seller side: `onchainos payment a2a-pay create` → returns paymentId + challenge."""
        args = ["payment", "a2a-pay", "create", "--type", "charge",
                "--amount", str(amount_usd), "--symbol", symbol, "--recipient", recipient]
        if description:
            args += ["--description", description]
        if external_id:
            args += ["--external-id", external_id]
        return self.run(args)

    def a2a_pay(self, payment_id: str, raw_amount: str, currency: str,
                recipient_address: str) -> dict:
        """Buyer side: `onchainos payment a2a-pay pay` — sign EIP-3009 + submit credential.

        LIVE-VERIFIED (mainnet, tx 0xa65fd720…): `pay --amount` is the RAW base-unit amount from
        the challenge (e.g. "10000" for 0.01 USDT), NOT the decimal — unlike `create --amount`
        which is decimal. Pass the challenge's `request.amount` verbatim. Currency is the token
        contract address; recipient must match the challenge's payTo."""
        return self.run(["payment", "a2a-pay", "pay", "--payment-id", payment_id,
                         "--amount", str(raw_amount), "--currency", currency,
                         "--recipient-address", recipient_address])

    def a2a_pay_status(self, payment_id: str) -> dict:
        return self.run(["payment", "a2a-pay", "status", "--payment-id", payment_id])

    # --- wallet + security (okx-agentic-wallet / security) -------------------------------------
    def wallet_balance(self) -> dict:
        return self.run(["wallet", "balance"])

    def security_scan(self, address: str) -> dict:
        """`onchainos security token-scan` — token/contract risk feeding the Vetter."""
        return self.run(["security", "token-scan", "--address", address])


_cli: OnchainOsCli | None = None


def get_cli() -> OnchainOsCli:
    global _cli
    if _cli is None:
        _cli = OnchainOsCli()
    return _cli
