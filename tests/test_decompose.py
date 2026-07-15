import pytest

from core.foreman import decompose, validate_dag
from core.schemas import PlannedNode


def test_decompose_produces_valid_dag_with_verify_node():
    plan = decompose("Research the top 3 rollups, then write a comparison post")
    assert len(plan.nodes) >= 2
    validate_dag(plan.nodes)  # should not raise
    last = plan.nodes[-1]
    assert last.capability == "coordination"
    assert last.needs_hire is False
    # final node depends on all prior nodes
    assert set(last.depends_on) == {n.key for n in plan.nodes[:-1]}


def test_sequential_dependencies_are_linked():
    plan = decompose("Gather market data then build a dashboard then deploy it")
    # n2 depends on n1, n3 depends on n2 (sequential markers = 'then')
    keyed = {n.key: n for n in plan.nodes}
    assert keyed["n2"].depends_on == ["n1"]
    assert keyed["n3"].depends_on == ["n2"]


def test_capability_mapping():
    plan = decompose("Audit the smart contract then write documentation")
    caps = {n.capability for n in plan.nodes}
    assert "security" in caps
    assert "content" in caps


def test_validate_dag_rejects_cycle():
    nodes = [
        PlannedNode(key="a", title="A", depends_on=["b"]),
        PlannedNode(key="b", title="B", depends_on=["a"]),
    ]
    with pytest.raises(ValueError):
        validate_dag(nodes)


def test_validate_dag_rejects_unknown_dep():
    nodes = [PlannedNode(key="a", title="A", depends_on=["zzz"])]
    with pytest.raises(ValueError):
        validate_dag(nodes)


def test_validate_dag_rejects_duplicate_keys():
    nodes = [PlannedNode(key="a", title="A"), PlannedNode(key="a", title="B")]
    with pytest.raises(ValueError):
        validate_dag(nodes)
