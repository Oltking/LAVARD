"""Intake path-classifier: pick the cheapest sufficient mode, conservatively.

direct_mcp / single_asp only when the plan is unambiguously one unit of work; every other
shape (multi-node, dependencies, sequencing language) falls back to orchestrate.
"""

from core.foreman.decompose import decompose
from core.intake.router import classify_path


def test_single_tool_call_routes_direct_mcp():
    plan = decompose("get the current BTC price")
    d = classify_path("get the current BTC price", plan.nodes)
    assert d.mode == "direct_mcp"
    assert d.short_circuits


def test_single_specialist_deliverable_routes_single_asp():
    plan = decompose("write a blog article about rollups")
    d = classify_path("write a blog article about rollups", plan.nodes)
    assert d.mode == "single_asp"
    assert d.short_circuits


def test_multistep_goal_orchestrates():
    goal = "research competitors then build a landing page and deploy it"
    plan = decompose(goal)
    d = classify_path(goal, plan.nodes)
    assert d.mode == "orchestrate"
    assert not d.short_circuits


def test_sequencing_language_forces_orchestrate_even_with_one_work_node():
    # a lone work verb but with "then" → not a single call
    goal = "look up the gas price then convert it"
    plan = decompose(goal)
    d = classify_path(goal, plan.nodes)
    assert d.mode == "orchestrate"


def test_no_specialist_work_orchestrates():
    # purely internal/coordination goals have zero hire nodes → keep the full (cheap) path
    plan = decompose("plan and organize my week")
    d = classify_path("plan and organize my week", plan.nodes)
    assert d.mode == "orchestrate"
    assert d.work_nodes == 0
