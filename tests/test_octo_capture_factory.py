from __future__ import annotations

import pytest

from velvet_audio_studio.adapters.alsa.capability_probe import PcmCapabilities
from velvet_audio_studio.adapters.alsa.card_discovery import AlsaCard
from velvet_audio_studio.adapters.audio_injector_octo import (
    AlsaOctoCaptureSource,
    AlsaPcmFormat,
    AudioInjectorOctoAdapter,
    OctoCaptureUnavailable,
    resolve_octo_capture,
    stable_alsa_pcm_name,
)


def _capabilities(
    device: str,
    *,
    channels_min: int = 2,
    channels_max: int = 8,
    rates: tuple[int, ...] = (8_000, 48_000),
    formats: tuple[str, ...] = ("S16_LE", "S32_LE"),
    available: bool = True,
    reasons: tuple[str, ...] = (),
) -> PcmCapabilities:
    return PcmCapabilities(
        device=device,
        direction="capture",
        channels_min=channels_min,
        channels_max=channels_max,
        rates=rates,
        formats=formats,
        available=available,
        degraded_reasons=reasons,
    )


def test_resolver_uses_stable_card_id_instead_of_numeric_index() -> None:
    card = AlsaCard(
        index=7,
        card_id="audioinjectoroc",
        name="AudioInjector Octo sound card",
    )
    probed_devices: list[tuple[str, str]] = []

    def probe(device: str, *, direction: str) -> PcmCapabilities:
        probed_devices.append((device, direction))
        return _capabilities(device)

    resolution = resolve_octo_capture(
        cards=(card,),
        period_frames=240,
        capability_probe=probe,
    )

    assert resolution.accepted is True
    assert resolution.card is card
    assert resolution.config is not None
    assert resolution.config.device == "hw:CARD=audioinjectoroc,DEV=0"
    assert resolution.config.period_frames == 240
    assert "hw:7" not in resolution.config.device
    assert probed_devices == [("hw:CARD=audioinjectoroc,DEV=0", "capture")]


def test_stable_pcm_name_can_request_plughw_without_losing_card_identity() -> None:
    card = AlsaCard(2, "audioinjectoroc", "AudioInjector Octo")

    assert stable_alsa_pcm_name(card) == "hw:CARD=audioinjectoroc,DEV=0"
    assert stable_alsa_pcm_name(card, pcm_device=1, plug=True) == (
        "plughw:CARD=audioinjectoroc,DEV=1"
    )


def test_missing_octo_fails_closed_with_clear_reason() -> None:
    resolution = resolve_octo_capture(
        cards=(AlsaCard(0, "bcm2835", "Pi HDMI"),),
        capability_probe=lambda *_args, **_kwargs: pytest.fail("probe should not run"),
    )

    assert resolution.accepted is False
    assert resolution.config is None
    assert "not found by identity" in resolution.degraded_reasons[0]
    with pytest.raises(OctoCaptureUnavailable, match="not found by identity"):
        resolution.require_source()


def test_capability_mismatch_rejects_source_before_arecord_start() -> None:
    card = AlsaCard(4, "audioinjectoroc", "AudioInjector Octo")

    resolution = resolve_octo_capture(
        cards=(card,),
        sample_rate_hz=48_000,
        sample_format=AlsaPcmFormat.S32_LE,
        capability_probe=lambda device, *, direction: _capabilities(
            device,
            channels_min=1,
            channels_max=2,
            rates=(44_100,),
            formats=("S16_LE",),
        ),
    )

    assert resolution.accepted is False
    assert any("maximum is 2" in reason for reason in resolution.degraded_reasons)
    assert any("S32_LE" in reason for reason in resolution.degraded_reasons)
    assert any("48000 Hz" in reason for reason in resolution.degraded_reasons)


def test_accepted_resolution_builds_concrete_capture_source() -> None:
    card = AlsaCard(3, "audioinjectoroc", "AudioInjector Octo")
    resolution = resolve_octo_capture(
        cards=(card,),
        capability_probe=lambda device, *, direction: _capabilities(device),
    )

    source = resolution.require_source(process_factory=lambda _command: object())

    assert isinstance(source, AlsaOctoCaptureSource)
    assert source.config.device == "hw:CARD=audioinjectoroc,DEV=0"


def test_explicit_package_preserves_original_adapter_import() -> None:
    adapter = AudioInjectorOctoAdapter()

    assert adapter.EXPECTED_INPUTS == 6
    assert adapter.EXPECTED_OUTPUTS == 8
