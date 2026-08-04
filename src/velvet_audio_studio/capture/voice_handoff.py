from __future__ import annotations

from dataclasses import dataclass

from velvet_audio_studio.capture.microphone_capture import VoiceCapturePacket


@dataclass(frozen=True)
class VoiceInputHandoff:
    event: str
    source_packet_event: str
    selected_channel_index: int | None
    selected_logical_name: str | None
    mono_samples: tuple[float, ...]
    raw_multichannel_samples: tuple[float, ...]
    confidence: float
    degraded_reasons: tuple[str, ...]


def prepare_voice_handoff(packet: VoiceCapturePacket) -> VoiceInputHandoff:
    reasons = list(packet.degraded_reasons)
    healthy = [
        channel
        for channel in packet.channels
        if not channel.muted and not channel.clipped
    ]

    if packet.stale:
        healthy = []
    if not healthy or packet.frames == 0:
        reasons.append("no healthy microphone candidate")
        return VoiceInputHandoff(
            event="audio.voice_input.degraded",
            source_packet_event=packet.event,
            selected_channel_index=None,
            selected_logical_name=None,
            mono_samples=(),
            raw_multichannel_samples=packet.interleaved_samples,
            confidence=0.0,
            degraded_reasons=tuple(dict.fromkeys(reasons)),
        )

    selected = max(healthy, key=lambda channel: (channel.rms, channel.peak, -channel.channel_index))
    channel_count = len(packet.channels)
    mono = packet.interleaved_samples[selected.channel_index::channel_count]

    # This is an initial deterministic candidate selector, not beamforming.
    # Confidence expresses signal usability only and remains deliberately conservative.
    confidence = min(1.0, selected.rms / 0.25) if selected.rms > 0 else 0.0
    if selected.rms < 0.01:
        reasons.append("selected microphone signal weak")

    return VoiceInputHandoff(
        event="audio.voice_input.ready",
        source_packet_event=packet.event,
        selected_channel_index=selected.channel_index,
        selected_logical_name=selected.logical_name,
        mono_samples=tuple(mono),
        raw_multichannel_samples=packet.interleaved_samples,
        confidence=confidence,
        degraded_reasons=tuple(dict.fromkeys(reasons)),
    )
