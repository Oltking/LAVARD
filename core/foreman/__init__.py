"""Foreman: decompose a verified goal into a task DAG; (later) query/vet/hire (§4.2)."""

from core.foreman.decompose import decompose, validate_dag  # noqa: F401
from core.foreman.market import find_candidates, rank_score  # noqa: F401
from core.foreman.hire import (  # noqa: F401
    HireOutcome,
    SignOffResult,
    hire_for_job,
    necessity_test,
    sign_off,
)
