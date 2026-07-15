"""Global aggregate learning improves predictions for a NEW user from collective patterns —
without any user content crossing (Tier 2)."""

from core.insights import record_workflow
from core.predict import predict_next


def test_global_pattern_helps_a_brand_new_owner():
    # many (anonymous) workflows pair engineering with security + qa
    for _ in range(5):
        record_workflow(["engineering", "security", "qa"])

    # a brand-new owner who has done only engineering benefits from the collective signal
    sugg = predict_next("totally_new_owner", ["engineering"])
    caps = {s.capability for s in sugg}
    assert "security" in caps and "qa" in caps
    # and it's explained as a cross-workflow pattern, not the owner's own history
    sec = next(s for s in sugg if s.capability == "security")
    assert sec.confidence > 0
