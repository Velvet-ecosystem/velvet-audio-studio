"""Studio-owned speech playback over one multichannel PCM sink."""

from __future__ import annotations

from dataclasses import dataclass
from threading import Event, Lock
from typing import Protocol

from velvet_audio_studio.adapters.alsa.pcm_format import AlsaPcmFormat
from velvet_audio_studio.contracts import AudioPriority, ChannelLease
from velvet_audio_studio.pcm import decode_pcm16_le, encode_routed_mono, resample_linear
from velvet_audio_studio.voice.synthesis import SynthesizedSpeech


class PcmPlaybackSink(Protocol):
    @property
    def sample_rate_hz(self) -> int:
        ...

    @property
    def channels(self) -> int:
        ...

    @property
    def sample_format(self) -> AlsaPcmFormat:
        ...

    @property
    def period_frames(self) -> int:
        ...

    def open(self) -> None:
        ...

    def write(self, payload: bytes) -> int:
        ...

    def close(self) -> None:
        ...


@dataclass(frozen=True)
class StudioPlaybackResult:
    request_id: str
    priority: AudioPriority
    output_channels: tuple[int, ...]
    source_sample_rate_hz: int
    playback_sample_rate_hz: int
    source_frames: int
    frames_written: int
    preempted: bool

    @property
    def playback_duration_ms(self) -> float:
        return self.frames_written * 1000.0 / self.playback_sample_rate_hz


@dataclass
class _ActivePlayback:
    priority: AudioPriority
    cancel: Event


class StudioSpeechPlaybackEngine:
    """Serialize voice clips into the Studio bus with bounded priority preemption.

    The engine is intentionally the only speech component allowed to touch the
    playback sink. Piper produces PCM; a valid Studio lease decides which output
    slots receive it. Higher-priority speech may cancel a lower-priority clip at
    a period boundary. This is a safe first playback policy, not the future
    concurrent music/voice mixer.
    """

    def __init__(self, sink: PcmPlaybackSink) -> None:
        if sink.sample_rate_hz <= 0:
            raise ValueError("playback sink sample rate must be positive")
        if sink.channels <= 0:
            raise ValueError("playback sink channel count must be positive")
        if sink.period_frames <= 0:
            raise ValueError("playback sink period_frames must be positive")
        self.sink = sink
        self._write_lock = Lock()
        self._state_lock = Lock()
        self._active: _ActivePlayback | None = None

    def play_speech(
        self,
        speech: SynthesizedSpeech,
        lease: ChannelLease,
    ) -> StudioPlaybackResult:
        self._validate(speech, lease)
        source_samples = decode_pcm16_le(speech.pcm_bytes)
        samples = resample_linear(
            source_samples,
            source_rate_hz=speech.sample_rate_hz,
            target_rate_hz=self.sink.sample_rate_hz,
        )

        cancel = Event()
        with self._state_lock:
            active = self._active
            if active is not None and lease.priority > active.priority:
                active.cancel.set()

        frames_written = 0
        preempted = False
        with self._write_lock:
            with self._state_lock:
                self._active = _ActivePlayback(priority=lease.priority, cancel=cancel)
            try:
                self.sink.open()
                period = self.sink.period_frames
                for start in range(0, len(samples), period):
                    if cancel.is_set():
                        preempted = True
                        break
                    chunk = samples[start : start + period]
                    payload = encode_routed_mono(
                        chunk,
                        total_channels=self.sink.channels,
                        output_channels=lease.output_channels,
                        sample_format=self.sink.sample_format,
                    )
                    frames_written += self.sink.write(payload)
            finally:
                with self._state_lock:
                    if self._active is not None and self._active.cancel is cancel:
                        self._active = None

        return StudioPlaybackResult(
            request_id=lease.request_id,
            priority=lease.priority,
            output_channels=lease.output_channels,
            source_sample_rate_hz=speech.sample_rate_hz,
            playback_sample_rate_hz=self.sink.sample_rate_hz,
            source_frames=speech.frame_count,
            frames_written=frames_written,
            preempted=preempted,
        )

    def close(self) -> None:
        with self._state_lock:
            if self._active is not None:
                self._active.cancel.set()
        with self._write_lock:
            self.sink.close()

    def _validate(self, speech: SynthesizedSpeech, lease: ChannelLease) -> None:
        if speech.channels != 1:
            raise ValueError("Studio speech playback currently requires mono synthesized PCM")
        if speech.sample_width_bytes != 2:
            raise ValueError("Studio speech playback currently requires S16_LE synthesized PCM")
        if not lease.output_channels:
            raise ValueError("Studio speech playback requires an output channel lease")
        if len(set(lease.output_channels)) != len(lease.output_channels):
            raise ValueError("lease output channels must be unique")
        if any(
            channel < 0 or channel >= self.sink.channels
            for channel in lease.output_channels
        ):
            raise ValueError("lease output channel is outside the playback sink")
