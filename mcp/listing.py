"""Go-live packaging (Phase 9): the artifacts that list LAVARD on OKX.AI and gate the launch.

Three pieces, all offline-reproducible and mock-backed until Q-API-1 is resolved:
  1. `build_listing()` — the marketplace listing manifest (identity, mode, MCP tool surface,
     pricing, dispute stake) LAVARD would publish as an ASP.
  2. `readiness_review()` — the internal pre-listing review: a checklist that must be all-green
     before publish (mirrors OKX.AI's "pass internal review" gate). Returns a verdict.
  3. `publish()` — the guarded go-live call. In mock/offline mode it returns a dry-run receipt;
     the real publish path is stubbed pending live-API verification (deliberately loud).

Nothing here fakes a live marketplace. `is_live()` reports the true backend selection so the
demo narrates honestly whether a real paid job is possible.
"""

from __future__ import annotations

from typing import Any

from core.config import get_settings
from mcp.server import list_tools
from onchain.factory import _live

LISTING_MODE = "agent-to-agent"  # LAVARD lists as A2A (QUESTIONS.md Q-LIST-1)
# VERIFIED (docs/vendor/okxai/onchainos-cli.md, okx.ai/tutorial/asp): A2A arbitration is filed by
# the ASP within 1 day of a rejected delivery and requires a 5% bounty deposit (refunded if the
# dispute succeeds), NOT a flat OKB stake / evaluator quorum (that was a Phase-0 guess).
ARBITRATION_BOUNTY_PCT = 5.0
ARBITRATION_FILE_WINDOW_DAYS = 1
REVIEW_SLA_HOURS = 24


def is_live() -> bool:
    """True only when real OKX credentials are set AND LAVARD_OKX_LIVE=1."""
    return _live()


def build_listing() -> dict[str, Any]:
    """The ASP listing manifest LAVARD publishes to the OKX.AI Agent Marketplace."""
    s = get_settings()
    return {
        "name": "LAVARD",
        "role": "ASP",
        "kind": "orchestration-agent",
        "mode": LISTING_MODE,
        "summary": "The general contractor for OKX.AI — vets, hires, and runs a crew of agents "
                   "under an accountable controller, settles onchain, and reuses portable memory.",
        "capabilities": ["orchestration", "vetting", "coordination", "settlement", "memory-reuse"],
        "mcp_tools": [t["name"] for t in list_tools()],
        "payer_wallet": "lavard.agentic.wallet",
        "pricing": {"model": "per-job", "job_budget_ceiling_usd": s.job_budget_usd},
        "dispute": {"escalates_to": "arbitration",
                    "file_within_days": ARBITRATION_FILE_WINDOW_DAYS,
                    "bounty_deposit_pct": ARBITRATION_BOUNTY_PCT,
                    "ratings": "on-chain"},
        "review_sla_hours": REVIEW_SLA_HOURS,
        "registration": {"surface": "onchainos CLI / skill-driven",
                         "cmd": "Help me register an A2A ASP on OKX.AI using OKX Agent Identity "
                                "from Onchain OS"},
        "skill_file": "mcp/skill.md",
    }


# Each gate: (id, human label, predicate over the listing manifest / environment).
def _gates(listing: dict[str, Any]) -> list[tuple[str, str, bool]]:
    return [
        ("identity", "Listing has name, role, and mode",
         bool(listing.get("name") and listing.get("role") and listing.get("mode"))),
        ("mcp_surface", "MCP tool surface is non-empty and callable",
         len(listing.get("mcp_tools", [])) >= 5),
        ("skill_file", "Published skill markdown is declared",
         listing.get("skill_file") == "mcp/skill.md"),
        ("pricing", "A job budget ceiling is set",
         listing.get("pricing", {}).get("job_budget_ceiling_usd", 0) > 0),
        ("arbitration", "A2A arbitration terms declared (bounty deposit + file window)",
         listing.get("dispute", {}).get("bounty_deposit_pct", 0) > 0
         and listing.get("dispute", {}).get("file_within_days", 0) > 0),
        ("governance", "Action Review + audit log are wired (approve_action tool present)",
         "approve_action" in listing.get("mcp_tools", [])),
        ("kill_switch", "Global kill-switch is exposed",
         "kill_switch" in listing.get("mcp_tools", [])),
    ]


def readiness_review(listing: dict[str, Any] | None = None) -> dict[str, Any]:
    """Internal pre-listing review. All gates must pass to be publish-ready."""
    listing = listing or build_listing()
    checks = [{"id": gid, "label": label, "passed": ok} for gid, label, ok in _gates(listing)]
    ready = all(c["passed"] for c in checks)
    return {
        "ready": ready,
        "verdict": "READY-TO-LIST" if ready else "BLOCKED",
        "checks": checks,
        "blocking": [c["id"] for c in checks if not c["passed"]],
    }


def publish() -> dict[str, Any]:
    """Guarded go-live. Requires a green readiness review; honest about mock vs live backend."""
    listing = build_listing()
    review = readiness_review(listing)
    if not review["ready"]:
        return {"published": False, "reason": "readiness review BLOCKED",
                "blocking": review["blocking"], "listing": listing}
    if not is_live():
        # Deliberately a dry run: real listing requires live OKX creds + the onchainos CLI.
        return {"published": False, "mode": "dry-run",
                "reason": "offline/mock backend — set OKX creds + LAVARD_OKX_LIVE=1 to publish",
                "listing": listing, "review": review}

    # Live path: OKX.AI ASP registration/listing is agent/CLI-driven and human-reviewed within
    # ~24h (verified) — it is NOT a single fire-and-forget API call. We verify the onchainos CLI is
    # installed + credentialed, then hand back the exact registration steps for the operator's
    # agent to run. This is honest: we don't claim a completed listing we can't synchronously make.
    from onchain.onchainos_cli import OnchainOsError, get_cli

    try:
        version = get_cli().health()
    except OnchainOsError as e:
        return {"published": False, "mode": "blocked", "reason": str(e), "listing": listing}
    return {
        "published": False, "mode": "ready-to-register", "onchainos_version": version,
        "reason": f"CLI ready. Registration is agent-driven + reviewed within "
                  f"{REVIEW_SLA_HOURS}h; run the steps below, then poll status.",
        "steps": [
            "Log in to Agentic Wallet on Onchain OS with my email",
            listing["registration"]["cmd"],
            "Help me list my ASP on OKX.AI using Onchain OS",
        ],
        "listing": listing, "review": review,
    }
