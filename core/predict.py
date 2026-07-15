"""Predictive next-task suggestions (§ vision — but money-safe).

From a user's workflow history, predict the tasks that usually FOLLOW what they just did — e.g. a
DeFi build is typically followed by an audit, tests, docs, and a deploy — and get them ready:
pre-plan the capability and pre-select a known-good agent from memory.

CRITICAL money-safety rule: this NEVER spends. It never hires, never opens escrow, never calls an
ASP. It produces *ready-to-run suggestions* the user approves — respecting the always-ask spending
governance. Speculative on-chain spend on work nobody asked for is explicitly out of scope.

Signal = three sources blended:
- a small domain prior (build→audit/test/deploy/docs, etc.) so it's useful from day one;
- the owner's OWN completed blueprints (personal co-occurrence), so it personalizes with history;
- the global anonymized co-occurrence model (core/insights) — patterns learned across all users
  WITHOUT any user content, so collective behavior sharpens predictions while sharing nothing.
Pre-selection reads the owner's OWN blueprint crew only — a pure private memory lookup, zero cost.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

from core.memory.store import get_memory

# Day-one domain prior: capability → capabilities that commonly follow it.
_DOMAIN_FOLLOWUPS: dict[str, list[str]] = {
    "engineering": ["security", "qa", "devops", "content"],   # build → audit, test, deploy, docs
    "finance":     ["security", "qa"],                        # onchain/defi → audit, test
    "design":      ["engineering", "content"],
    "content":     ["marketing"],
    "data":        ["qa", "content"],
    "research":    ["content", "design"],
    "security":    ["qa"],
}


@dataclass
class Suggestion:
    capability: str
    reason: str
    confidence: float                       # 0..1
    preselected: dict | None = None         # {agent_id, name, score} from memory, or None
    prepared: bool = True                   # ready to run on approval (nothing spent)

    def to_dict(self) -> dict:
        return asdict(self)


def _owner_cooccurrence(owner_id: str, mem) -> dict[tuple[str, str], int]:
    """Count capability pairs that co-occur across the owner's completed blueprints."""
    pairs: dict[tuple[str, str], int] = {}
    for pb in mem.list_playbooks(owner_id):
        roles = sorted(set(pb.roles))
        for i, a in enumerate(roles):
            for b in roles[i + 1:]:
                pairs[(a, b)] = pairs.get((a, b), 0) + 1
                pairs[(b, a)] = pairs.get((b, a), 0) + 1
    return pairs


def _best_crew_for(owner_id: str, capability: str, mem) -> dict | None:
    """Highest-scoring known-good agent for a capability across the owner's blueprints (memory
    read only — no marketplace call, no spend)."""
    best: dict | None = None
    for pb in mem.list_playbooks(owner_id):
        for c in pb.crew:
            if c.get("capability") == capability:
                if best is None or c.get("score", 0) > best.get("score", 0):
                    best = c
    return best


def predict_next(owner_id: str, current_roles: list[str], *, mem=None,
                 top_k: int = 4) -> list[Suggestion]:
    """Suggest likely follow-on capabilities for a user who just did `current_roles`.

    Pure and cost-free: reads memory, returns prepared suggestions. Approving one is what triggers
    an actual (paid, governed) run."""
    from core.insights import global_followups

    mem = mem or get_memory()
    have = set(current_roles)
    cooc = _owner_cooccurrence(owner_id, mem)

    scores: dict[str, float] = {}
    reasons: dict[str, str] = {}
    for role in current_roles:
        # domain prior (day one)
        for follow in _DOMAIN_FOLLOWUPS.get(role, []):
            if follow in have:
                continue
            scores[follow] = scores.get(follow, 0.0) + 0.5
            reasons[follow] = f"{follow} commonly follows {role} work"
        # the owner's OWN history (personal, private)
        for (a, b), n in cooc.items():
            if a == role and b not in have:
                scores[b] = scores.get(b, 0.0) + min(0.5, 0.1 * n)
                reasons[b] = f"you've paired {b} with {role} in {n} past workflow(s)"
        # global anonymized aggregate (everyone's shapes, no user content)
        for follow, weight in global_followups(role).items():
            if follow in have:
                continue
            scores[follow] = scores.get(follow, 0.0) + 0.3 * weight
            reasons.setdefault(follow, f"{follow} commonly follows {role} across many workflows")

    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)[:top_k]
    out: list[Suggestion] = []
    for cap, raw in ranked:
        out.append(Suggestion(
            capability=cap,
            reason=reasons.get(cap, f"often follows {', '.join(current_roles)}"),
            confidence=min(1.0, raw),
            preselected=_best_crew_for(owner_id, cap, mem),
        ))
    return out


def predict_for_job(job_id: str, *, store=None, mem=None) -> list[Suggestion]:
    """Suggestions for what to do next after a specific job, from its capability set."""
    from core.store import get_store

    store = store or get_store()
    job = store.get_job(job_id)
    if job is None:
        return []
    roles = sorted({n["capability"] for n in job["nodes"] if n["capability"] != "coordination"})
    return predict_next(job["owner_id"], roles, mem=mem)
