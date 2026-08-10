from __future__ import annotations

from struct import unpack
from threading import Event, Thread
from time import monotonic, sleep

import pytest

from velvet_audio_studio.adapters.alsa.pcm_format import AlsaPcmFormat
from velvet_audio_studio.contracts import AudioPriority, ChannelLease
from velvet_audio_studio.pcm import encode_pcm16_le
from velvet_audio_studio.playback_engine import StudioSpeechPlaybackEngine
from velvet_audio_studio.voice.synthesis import SynthesizedSpeech


class FakeSink:
    sample_rate_hz = 48_000
    channels = 8
    sample_format = AlsaPcmFormat.S16_LE
    period_frames = 2

    def __init__(self) -> None:
        self.opened = False
        self.payloads: list[bytes] = []

    def open(self) -> None:
        self.opened = True

    def write(self, payload: bytes) -> int:
        assert self.opened is True
        self.payloads.append(payload)
        return len(payload) // (self.channels * self.sample_format.bytes_per_sample)

    def close(self) -> None:
        self.opened = False


class BlockingFirstWriteSink(FakeSink):
    period_frames = 1

    def __init__(self) -> None:
        super().__init__()
        self.first_write_started = Event()
        self.release_first_write = Event()
        self._blocked_once = False

    def write(self, payload: bytes) -> int:
        if not self._blocked_once:
            self._blocked_once = True
            self.first_write_started.set()
            assert self.release_first_write.wait(timeout=2.0)
        return super().write(payload)


def _speech(samples: tuple[float, ...], *, sample_rate_hz: int = 48_000) -> SynthesizedSpeech:
    return SynthesizedSpeech(
        model_id="velvet-test",
        profile_id="owner_default",
        sample_rate_hz=sample_rate_hz,
        sample_width_bytes=2,
        channels=1,
        pcm_bytes=encode_pcm16_le(samples),
        text_char_count=12,
    )


def _lease(
    priority: AudioPriority,
    outputs: tuple[int, ...],
    *,
    request_id: str,
) -> ChannelLease:
    return ChannelLease(
        request_id=request_id,
        requester="Velvet",
        input_channels=(),
        output_channels=outputs,
        priority=priority,
    )


def test_engine_routes_mono_speech_only_into_leased_octo_slots() -> None:
    sink = FakeSink()
    engine = StudioSpeechPlaybackEngine(sink)

    result = engine.play_speech(
        _speech((0.5, -0.5)),
        _lease(AudioPriority.VELVET_VOICE, (1, 4), request_id="voice-1"),
    )

    assert result.frames_written == 2
    assert result.output_channels == (1, 4)
    assert result.preempted is False
    assert len(sink.payloads) == 1

    values = unpack("<16h", sink.payloads[0])
    first = values[:8]
    second = values[8:]
    assert first[0] == 0
    assert first[1] > 16_000
    assert first[4] == first[1]
    assert sum(value != 0 for value in first) == 2
    assert second[1] < -16_000
    assert second[4] == second[1]
    assert sum(value != 0 for value in second) == 2


def test_engine_resamples_piper_pcm_to_the_accepted_playback_rate() -> None:
    sink = FakeSink()
    sink.sample_rate_hz = 48_000
    engine = StudioSpeechPlaybackEngine(sink)

    result = engine.play_speech(
        _speech((0.0, 1.0), sample_rate_hz=24_000),
        _lease(AudioPriority.VELVET_VOICE, (4,), request_id="voice-2"),
    )

    assert result.source_frames == 2
    assert result.frames_written == 4
    assert result.playback_sample_rate_hz == 48_000
    assert len(sink.payloads) == 2


def test_higher_priority_speech_preempts_at_a_period_boundary() -> None:
    sink = BlockingFirstWriteSink()
    engine = StudioSpeechPlaybackEngine(sink)
    low_result: list[object] = []
    high_result: list[object] = []

    low = Thread(
        target=lambda: low_result.append(
            engine.play_speech(
                _speech((0.1, 0.2, 0.3, 0.4)),
                _lease(AudioPriority.MUSIC, (0, 1), request_id="music-like-voice"),
            )
        )
    )
    low.start()
    assert sink.first_write_started.wait(timeout=2.0)

    high = Thread(
        target=lambda: high_result.append(
            engine.play_speech(
                _speech((0.8, 0.8)),
                _lease(AudioPriority.SAFETY, (4, 6), request_id="safety-voice"),
            )
        )
    )
    high.start()

    deadline = monotonic() + 2.0
    while monotonic() < deadline:
        active = engine._active
        if active is not None and active.cancel.is_set():
            break
        sleep(0.001)
    else:
        raise AssertionError("higher-priority playback did not request preemption")

    sink.release_first_write.set()
    low.join(timeout=2.0)
    high.join(timeout=2.0)

    assert low.is_alive() is False
    assert high.is_alive() is False
    assert low_result[0].preempted is True
    assert low_result[0].frames_written == 1
    assert high_result[0].preempted is False
    assert high_result[0].frames_written == 2


def test_engine_rejects_speech_without_a_valid_output_lease() -> None:
    engine = StudioSpeechPlaybackEngine(FakeSink())

    with pytest.raises(ValueError, match="output channel lease"):
        engine.play_speech(
            _speech((0.1,)),
            _lease(AudioPriority.VELVET_VOICE, (), request_id="no-output"),
        )


def test_engine_close_cancels_active_playback_and_closes_single_sink() -> None:
    sink = FakeSink()
    engine = StudioSpeechPlaybackEngine(sink)
    engine.play_speech(
        _speech((0.1,)),
        _lease(AudioPriority.VELVET_VOICE, (4,), request_id="voice-3"),
    )
    assert sink.opened is True

    engine.close()

    assert sink.opened is False
