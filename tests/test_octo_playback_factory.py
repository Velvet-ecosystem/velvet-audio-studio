from __future__ import annotations

from velvet_audio_studio.adapters.alsa.capability_probe import PcmCapabilities
from velvet_audio_studio.adapters.alsa.card_discovery import AlsaCard
from velvet_audio_studio.adapters.alsa.pcm_format import AlsaPcmFormat
from velvet_audio_studio.adapters.audio_injector_octo.playback_factory import (
    OctoPlaybackUnavailable,
    resolve_octo_playback,
)


def _card() -> AlsaCard:
    return AlsaCard(index=2, card_id="audioinjectoroc", name="AudioInjector Octo")


def test_resolver_accepts_expected_eight_channel_playback_capability() -> None:
    observed: list[tuple[str, str]] = []

    def probe(device: str, *, direction: str) -> PcmCapabilities:
        observed.append((device, direction))
        return PcmCapabilities(
            device=device,
            direction=direction,
            channels_min=2,
            channels_max=8,
            rates=(48_000,),
            formats=("S16_LE", "S32_LE"),
            available=True,
        )

    resolution = resolve_octo_playback(
        cards=(_card(),),
        sample_format=AlsaPcmFormat.S32_LE,
        capability_probe=probe,
    )

    assert resolution.accepted is True
    assert resolution.config is not None
    assert resolution.config.channels == 8
    assert resolution.config.device == "hw:CARD=audioinjectoroc,DEV=0"
    assert observed == [("hw:CARD=audioinjectoroc,DEV=0", "playback")]
    assert resolution.require_sink().channels == 8


def test_resolver_rejects_device_that_cannot_expose_eight_channels() -> None:
    def probe(device: str, *, direction: str) -> PcmCapabilities:
        return PcmCapabilities(
            device=device,
            direction=direction,
            channels_min=2,
            channels_max=6,
            rates=(48_000,),
            formats=("S32_LE",),
            available=True,
        )

    resolution = resolve_octo_playback(cards=(_card(),), capability_probe=probe)

    assert resolution.accepted is False
    assert "device maximum is 6" in "; ".join(resolution.degraded_reasons)
    try:
        resolution.require_sink()
    except OctoPlaybackUnavailable:
        pass
    else:
        raise AssertionError("Expected rejected playback resolution to fail closed")


def test_resolver_rejects_missing_card_without_probing() -> None:
    probed = False

    def probe(device: str, *, direction: str) -> PcmCapabilities:
        nonlocal probed
        probed = True
        raise AssertionError("probe should not run")

    resolution = resolve_octo_playback(cards=(), capability_probe=probe)

    assert resolution.accepted is False
    assert resolution.card is None
    assert probed is False
