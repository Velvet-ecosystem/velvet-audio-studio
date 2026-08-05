import pytest

from velvet_audio_studio.runtime.court_routing import (
    CourtDecision,
    CourtDisposition,
)


def test_raw_disposition_value_is_rejected() -> None:
    with pytest.raises(TypeError, match="Court disposition"):
        CourtDecision(  # type: ignore[arg-type]
            disposition="approved",
            court_receipt_id="court-receipt-1",
            capability="audio.route",
        )


def test_blank_optional_approval_reason_is_rejected() -> None:
    with pytest.raises(ValueError, match="approval reason"):
        CourtDecision(
            disposition=CourtDisposition.APPROVED,
            court_receipt_id="court-receipt-1",
            capability="audio.route",
            reason="   ",
        )


def test_approval_reason_is_trimmed_as_receipt_evidence() -> None:
    decision = CourtDecision(
        disposition=CourtDisposition.APPROVED,
        court_receipt_id="  court-receipt-1  ",
        capability="  audio.route  ",
        reason="  owner-authorized route  ",
    )

    assert decision.court_receipt_id == "court-receipt-1"
    assert decision.capability == "audio.route"
    assert decision.reason == "owner-authorized route"
