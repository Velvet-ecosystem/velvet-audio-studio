from __future__ import annotations

import pytest

from velvet_audio_studio.channel_registry import ChannelRegistry, ChannelUnavailable
from velvet_audio_studio.contracts import AudioPriority, StudioRequest
from velvet_audio_studio.session_manager import StudioSessionManager


def _request(
    request_id: str,
    priority: AudioPriority,
    *,
    allow_preemption: bool,
) -> StudioRequest:
    return StudioRequest(
        requester="Velvet",
        purpose="speech",
        priority=priority,
        output_channels=1,
        preferred_output_channels=(4,),
        request_id=request_id,
        allow_preemption=allow_preemption,
    )


def test_explicit_higher_priority_request_takes_conflicting_lower_output_lease() -> None:
    registry = ChannelRegistry(input_count=6, output_count=8)
    manager = StudioSessionManager(registry)
    low = manager.book(
        _request("low", AudioPriority.VELVET_VOICE, allow_preemption=False)
    )

    high = manager.book(
        _request("high", AudioPriority.SAFETY, allow_preemption=True)
    )

    assert low.request_id == "low"
    assert high.request_id == "high"
    assert high.output_channels == (4,)
    assert registry.leases == (high,)
    assert manager.release("low") is None


def test_equal_priority_request_cannot_preempt_conflicting_lease() -> None:
    registry = ChannelRegistry(input_count=6, output_count=8)
    manager = StudioSessionManager(registry)
    low = manager.book(
        _request("first", AudioPriority.SAFETY, allow_preemption=False)
    )

    with pytest.raises(ChannelUnavailable):
        manager.book(
            _request("second", AudioPriority.SAFETY, allow_preemption=True)
        )

    assert registry.leases == (low,)


def test_preemption_must_be_explicit_even_for_higher_priority() -> None:
    registry = ChannelRegistry(input_count=6, output_count=8)
    manager = StudioSessionManager(registry)
    low = manager.book(
        _request("low", AudioPriority.MUSIC, allow_preemption=False)
    )

    with pytest.raises(ChannelUnavailable):
        manager.book(
            _request("high", AudioPriority.SAFETY, allow_preemption=False)
        )

    assert registry.leases == (low,)
