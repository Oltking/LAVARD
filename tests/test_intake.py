from core.intake import verify_goal


def test_verify_goal_restates_and_defines_success():
    r = verify_goal("Build a landing page for my token")
    assert r.restated_goal
    assert r.success_criteria, "must define measurable success criteria"
    assert r.assumptions


def test_verify_goal_surfaces_missing_budget_and_deadline():
    r = verify_goal("Write a whitepaper")
    joined = " ".join(r.open_questions).lower()
    assert "budget" in joined
    assert "deadline" in joined


def test_verify_goal_no_budget_question_when_budget_mentioned():
    r = verify_goal("Write a whitepaper under a $50 budget by Friday")
    joined = " ".join(r.open_questions).lower()
    assert "budget" not in joined
    assert "deadline" not in joined


def test_empty_goal_rejected():
    import pytest

    with pytest.raises(ValueError):
        verify_goal("   ")
