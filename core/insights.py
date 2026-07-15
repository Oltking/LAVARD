"""Global aggregate learning (privacy Tier 2) — "train on what everyone did, expose nothing".

The system learns collective *patterns* — which capabilities tend to follow which — so predictions
and planning improve for everyone as usage grows. The unit of learning is a STATISTIC, never a
record: only capability-pair counts are stored (see store.insights_cooccurrence). There is no
owner id, no goal text, and no deliverable anywhere in this layer, so no individual user's work can
be reconstructed from it and nothing is ever handed from one user to another.

This is the deliberate, safe complement to the strictly owner-scoped private memory (blueprints and
facts), which is NEVER shared across users. See docs/privacy.md and [[lavard-project]].
"""

from __future__ import annotations

from core.store import get_store


def record_workflow(capabilities: list[str], store=None) -> None:
    """Fold a completed workflow's capability set into the global anonymized co-occurrence model.
    Owner and content are intentionally NOT passed — only the capability shape is learned."""
    (store or get_store()).bump_cooccurrence(capabilities)


def global_followups(capability: str, store=None) -> dict[str, float]:
    """Normalized global signal for what capabilities tend to follow `capability` (0..1 per peer),
    learned across all users' anonymized workflow shapes."""
    counts = (store or get_store()).get_cooccurrence(capability)
    total = sum(counts.values())
    if not total:
        return {}
    return {cap: n / total for cap, n in counts.items()}
