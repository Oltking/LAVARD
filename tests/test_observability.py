"""Observability: the alert seam captures money/integrity events and survives a broken sink."""

from core.observability import alert, set_alert_sink


def test_custom_sink_receives_structured_events():
    captured = []
    set_alert_sink(captured.append)
    try:
        ev = alert("escrow_released", severity="notice", job_id="j1", amount_usd=12.0)
        assert ev["event"] == "escrow_released" and ev["severity"] == "notice"
        assert captured and captured[0]["amount_usd"] == 12.0
    finally:
        set_alert_sink(None)


def test_broken_sink_never_raises():
    def boom(_event):
        raise RuntimeError("pager down")

    set_alert_sink(boom)
    try:
        # a broken alert sink must never break the money path
        ev = alert("settlement_failed", severity="critical", amount_usdt=1.0)
        assert ev["event"] == "settlement_failed"
    finally:
        set_alert_sink(None)
