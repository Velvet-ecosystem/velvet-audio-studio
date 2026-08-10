from __future__ import annotations

from io import BytesIO
from typing import Sequence

import pytest

from velvet_audio_studio.adapters.alsa.pcm_format import AlsaPcmFormat
from velvet_audio_studio.adapters.audio_injector_octo.alsa_playback import (
    AlsaOctoPlaybackSink,
    AlsaPlaybackConfig,
    AlsaPlaybackError,
)


class FakeProcess:
    def __init__(self) -> None:
        self.stdin = BytesIO()
        self.stderr = BytesIO()
        self.return_code: int | None = None
        self.terminated = False
        self.killed = False

    def poll(self) -> int | None:
        return self.return_code

    def wait(self, timeout: float | None = None) -> int:
        self.return_code = 0
        return 0

    def terminate(self) -> None:
        self.terminated = True
        self.return_code = 0

    def kill(self) -> None:
        self.killed = True
        self.return_code = -9


def test_playback_config_builds_one_raw_eight_channel_aplay_stream() -> None:
    config = AlsaPlaybackConfig(
        device="hw:CARD=audioinjectoroc,DEV=0",
        sample_rate_hz=48_000,
        channels=8,
        period_frames=480,
        sample_format=AlsaPcmFormat.S32_LE,
    )

    assert config.command() == (
        "aplay",
        "--quiet",
        "--device",
        "hw:CARD=audioinjectoroc,DEV=0",
        "--file-type",
        "raw",
        "--format",
        "S32_LE",
        "--rate",
        "48000",
        "--channels",
        "8",
        "--period-size",
        "480",
        "-",
    )
    assert config.frame_bytes == 32
    assert config.period_bytes == 15_360


def test_sink_opens_once_and_writes_frame_aligned_pcm() -> None:
    process = FakeProcess()
    commands: list[tuple[str, ...]] = []

    def factory(command: Sequence[str]) -> FakeProcess:
        commands.append(tuple(command))
        return process

    sink = AlsaOctoPlaybackSink(
        AlsaPlaybackConfig(
            device="hw:CARD=audioinjectoroc,DEV=0",
            sample_format=AlsaPcmFormat.S16_LE,
            period_frames=2,
        ),
        process_factory=factory,
    )
    payload = bytes(range(32))

    assert sink.write(payload) == 2
    sink.open()

    assert sink.is_open is True
    assert sink.written_frames == 2
    assert len(commands) == 1
    assert process.stdin.getvalue() == payload

    sink.close()
    assert sink.is_open is False


def test_sink_rejects_partial_multichannel_frame_before_opening() -> None:
    opened = False

    def factory(command: Sequence[str]) -> FakeProcess:
        nonlocal opened
        opened = True
        return FakeProcess()

    sink = AlsaOctoPlaybackSink(
        AlsaPlaybackConfig(device="hw:CARD=audioinjectoroc,DEV=0"),
        process_factory=factory,
    )

    with pytest.raises(AlsaPlaybackError, match="partial frame"):
        sink.write(b"not-a-complete-frame")
    assert opened is False


def test_sink_reports_process_exit_before_write() -> None:
    process = FakeProcess()

    def factory(command: Sequence[str]) -> FakeProcess:
        return process

    sink = AlsaOctoPlaybackSink(
        AlsaPlaybackConfig(
            device="hw:CARD=audioinjectoroc,DEV=0",
            sample_format=AlsaPcmFormat.S16_LE,
        ),
        process_factory=factory,
    )
    sink.open()
    process.return_code = 7
    process.stderr.write(b"device disappeared")
    process.stderr.seek(0)

    with pytest.raises(AlsaPlaybackError, match="code 7"):
        sink.write(b"\x00" * 16)
