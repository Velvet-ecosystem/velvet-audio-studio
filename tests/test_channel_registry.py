from velvet_audio_studio.channel_registry import ChannelRegistry, ChannelUnavailable
from velvet_audio_studio.contracts import AudioPriority, StudioRequest


def test_allocates_and_releases_channels() -> None:
    registry = ChannelRegistry(input_count=2, output_count=4)
    request = StudioRequest(
        requester="Lyra",
        purpose="voice playback",
        priority=AudioPriority.VELVET_VOICE,
        output_channels=2,
    )

    lease = registry.allocate(request)

    assert lease.output_channels == (0, 1)
    assert registry.release(request.request_id) == lease
    assert registry.leases == ()


def test_refuses_overbooking() -> None:
    registry = ChannelRegistry(input_count=1, output_count=1)
    registry.allocate(
        StudioRequest(
            requester="Echo",
            purpose="music",
            priority=AudioPriority.MUSIC,
            output_channels=1,
        )
    )

    try:
        registry.allocate(
            StudioRequest(
                requester="Temperance",
                purpose="alert",
                priority=AudioPriority.SAFETY,
                output_channels=1,
            )
        )
    except ChannelUnavailable:
        pass
    else:
        raise AssertionError("Expected channel allocation to fail")
