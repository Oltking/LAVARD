"""LAVARD OS overview + network-effects telemetry (§ vision: one OS, invisible engines).

Presents LAVARD as a single layered operating system and instruments the three network effects
that make it compound — and defensible — as adoption grows:

  Memory Network      — every completed workflow (blueprint + facts) makes future work faster and
                        cheaper: measured by reuse events and blueprint reuse.
  Liquidity Network   — more users → more batching/sharing opportunities → lower cost for everyone:
                        measured by TheHouse aggregation savings + Router/Intelligence-Exchange
                        shared-answer savings.
  Reputation Network  — more executions → better performance data → sharper agent selection:
                        measured by agents scored and executions recorded.

Everything here is read-only aggregation over what actually happened — no fabricated numbers.
"""

from __future__ import annotations

from core.memory.store import get_memory
from core.store import get_store

_LAYERS = [
    {"layer": "LAVARD OS", "role": "single interface — user states a goal, LAVARD does the rest"},
    {"layer": "Planner", "role": "verify-first intake + task-graph decomposition"},
    {"layer": "Optimization Engine", "role": "cost/speed/quality/reputation scoring per preference"},
    {"layer": "Memory System", "role": "reusable workflow blueprints, facts, preferences"},
    {"layer": "Reputation Graph", "role": "multi-dimensional, execution-history-backed agent scoring"},
    {"layer": "Workflow Engine + Security", "role": "controller-mediated Room, referee, governance"},
    {"layer": "Agent Router", "role": "cheapest-accurate routing, semantic cache, dedup"},
    {"layer": "TheHouse Broker", "role": "invisible request batching + shared idempotent answers"},
    {"layer": "OKX AI Marketplace", "role": "specialist ASPs, onchain identity + settlement"},
]


def os_overview(exchange=None) -> dict:
    """The layered OS + live network-effect metrics. `exchange` is an optional in-process
    IntelligenceExchange whose shared-answer savings are folded into the liquidity network."""
    store = get_store()
    net = store.network_metrics()
    mem = get_memory().summary()

    exchange_stats = exchange.stats.to_dict() if exchange is not None else {
        "upstream_calls": 0, "shared_hits": 0, "calls_saved": 0, "cost_saved": 0.0}

    jobs = max(1, net["jobs"])
    liquidity_saved = round(net["thehouse_saved_usd"] + net["router_saved_usd"]
                            + exchange_stats["cost_saved"], 4)

    return {
        "product": "LAVARD — Autonomous AI Operating System for OKX AI",
        "one_liner": ("Users give it a goal. It plans the work, batches requests to cut cost, hires "
                      "and runs the best specialist agents, settles onchain, and compounds memory "
                      "so the whole system gets smarter and cheaper as more people use it."),
        "layers": _LAYERS,
        "network_effects": {
            "memory": {
                "blueprints": mem["blueprints"],
                "blueprint_uses": mem["blueprint_uses"],
                "facts": mem["facts"],
                "reuse_events": net["memory_reuse_events"],
                "note": "every completed workflow makes future work faster and cheaper",
            },
            "liquidity": {
                "thehouse_saved_usd": net["thehouse_saved_usd"],
                "router_saved_usd": net["router_saved_usd"],
                "intelligence_exchange": exchange_stats,
                "total_saved_usd": liquidity_saved,
                "note": "more users → more batching/sharing → lower cost for everyone",
            },
            "reputation": {
                "agents_scored": net["agents_scored"],
                "executions_recorded": net["executions_recorded"],
                "executions_delivered": net["executions_delivered"],
                "optimizer_selections": net["optimizer_selections"],
                "note": "more executions → better data → sharper agent selection",
            },
        },
        "jobs_run": net["jobs"],
    }
