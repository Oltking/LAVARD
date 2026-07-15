from core.service import get_job, submit_goal


def test_submit_goal_end_to_end():
    view = submit_goal("Research 3 competitors then draft a positioning brief")
    assert view.status == "decomposed"
    assert view.planner == "heuristic"  # no model configured in tests
    assert view.restated_goal
    assert view.success_criteria
    assert len(view.nodes) >= 2
    # roundtrip from the DB
    again = get_job(view.id)
    assert again is not None
    assert again.id == view.id
    assert len(again.nodes) == len(view.nodes)


def test_submit_goal_rejects_empty():
    import pytest

    with pytest.raises(ValueError):
        submit_goal("")
