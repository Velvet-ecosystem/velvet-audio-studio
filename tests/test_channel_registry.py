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


def test_allocates_requested_preferred_output_slots() -> None:
    registry = ChannelRegistry(input_count=2, output_count=8)
    request = StudioRequest(
        requester="Velvet",
        purpose="center voice",
        priority=AudioPriority.VELVET_VOICE,
        output_channels=2,
        preferred_output_channels=(4, 6),
    )

    lease = registry.allocate(request)

    assert lease.output_channels == (4, 6)


def test_refuses_preferred_output_that_is_already_leased() -> None:
    registry = ChannelRegistry(input_count=1, output_count=8)
    registry.allocate(
        StudioRequest(
            requester="Velvet",
            purpose="voice",
            priority=AudioPriority.VELVET_VOICE,
            output_channels=1,
            preferred_output_channels=(4,),
        )
    )

    try:
        registry.allocate(
            StudioRequest(
                requester="Temperance",
                purpose="alert",
                priority=AudioPriority.SAFETY,
                output_channels=1,
                preferred_output_channels=(4,),
            )
        )
    except ChannelUnavailable:
        pass
    else:
        raise AssertionError("Expected preferred channel allocation to fail")


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
