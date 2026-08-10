"""Resolve an accepted Octo playback sink from ALSA identity and capability data."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Sequence

from velvet_audio_studio.adapters.alsa.capability_probe import PcmCapabilities, probe_pcm
from velvet_audio_studio.adapters.alsa.card_discovery import (
    AlsaCard,
    find_card,
    list_alsa_cards,
)
from velvet_audio_studio.adapters.alsa.pcm_format import AlsaPcmFormat

from .alsa_playback import AlsaOctoPlaybackSink, AlsaPlaybackConfig
from .capture_factory import stable_alsa_pcm_name


class OctoPlaybackUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class OctoPlaybackResolution:
    card: AlsaCard | None
    config: AlsaPlaybackConfig | None
    capabilities: PcmCapabilities | None
    accepted: bool
    degraded_reasons: tuple[str, ...]

    def require_sink(self, **sink_kwargs: object) -> AlsaOctoPlaybackSink:
        if not self.accepted or self.config is None:
            reason = "; ".join(self.degraded_reasons) or "Octo playback was not accepted"
            raise OctoPlaybackUnavailable(reason)
        return AlsaOctoPlaybackSink(self.config, **sink_kwargs)


CapabilityProbe = Callable[..., PcmCapabilities]


def resolve_octo_playback(
    *,
    cards: Sequence[AlsaCard] | None = None,
    identity_terms: tuple[str, ...] = ("audioinjector", "octo"),
    pcm_device: int = 0,
    plug: bool = False,
    sample_rate_hz: int = 48_000,
    period_frames: int = 480,
    sample_format: AlsaPcmFormat = AlsaPcmFormat.S32_LE,
    capability_probe: CapabilityProbe = probe_pcm,
) -> OctoPlaybackResolution:
    """Find the Octo by identity, probe playback, and validate eight-channel use."""
    discovered_cards = tuple(list_alsa_cards() if cards is None else cards)
    card = find_card(discovered_cards, identity_terms=identity_terms)
    if card is None:
        return OctoPlaybackResolution(
            card=None,
            config=None,
            capabilities=None,
            accepted=False,
            degraded_reasons=("Audio Injector Octo ALSA card was not found by identity",),
        )

    config = AlsaPlaybackConfig(
        device=stable_alsa_pcm_name(card, pcm_device=pcm_device, plug=plug),
        sample_rate_hz=sample_rate_hz,
        period_frames=period_frames,
        sample_format=sample_format,
    )
    capabilities = capability_probe(config.device, direction="playback")
    reasons = list(capabilities.degraded_reasons)

    if not capabilities.available and not reasons:
        reasons.append("ALSA playback capability probe was unavailable")

    if capabilities.channels_min is not None and config.channels < capabilities.channels_min:
        reasons.append(
            f"Octo playback requires {config.channels} channels but device minimum is "
            f"{capabilities.channels_min}"
        )
    if capabilities.channels_max is not None and config.channels > capabilities.channels_max:
        reasons.append(
            f"Octo playback requires {config.channels} channels but device maximum is "
            f"{capabilities.channels_max}"
        )

    if capabilities.formats and config.sample_format.value not in capabilities.formats:
        reasons.append(
            f"playback format {config.sample_format.value} was not reported by the device"
        )

    if capabilities.rates and not _rate_supported(capabilities.rates, config.sample_rate_hz):
        reasons.append(
            f"playback rate {config.sample_rate_hz} Hz was not reported by the device"
        )

    return OctoPlaybackResolution(
        card=card,
        config=config,
        capabilities=capabilities,
        accepted=capabilities.available and not reasons,
        degraded_reasons=tuple(dict.fromkeys(reasons)),
    )


def _rate_supported(rates: tuple[int, ...], requested: int) -> bool:
    if requested in rates:
        return True
    if len(rates) == 2:
        return min(rates) <= requested <= max(rates)
    return False
