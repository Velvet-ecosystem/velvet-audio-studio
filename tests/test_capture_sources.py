from __future__ import annotations

from io import BytesIO
import struct
import subprocess

import pytest

from velvet_audio_studio.adapters.audio_injector_octo.alsa_capture import (
    AlsaCaptureConfig,
    AlsaCaptureError,
    AlsaOctoCaptureSource,
    AlsaPcmFormat,
    decode_interleaved_pcm,
)
from velvet_audio_studio.simulated.capture_source import (
    SimulatedCaptureSource,
    simulated_six_channel_frame,
)


class FakeProcess:
    def __init__(
        self,
        payload: bytes,
        *,
        exit_when_exhausted: int | None = None,
        stderr: bytes = b"",
        wait_times_out: bool = False,
    ) -> None:
        self.stdout = BytesIO(payload)
        self.stderr = BytesIO(stderr)
        self._payload_size = len(payload)
        self._exit_when_exhausted = exit_when_exhausted
        self._return_code: int | None = None
        self.wait_times_out = wait_times_out
        self.terminated = False
        self.killed = False

    def poll(self) -> int | None:
        if (
            self._return_code is None
            and self._exit_when_exhausted is not None
            and self.stdout.tell() >= self._payload_size
        ):
            self._return_code = self._exit_when_exhausted
        return self._return_code

    def terminate(self) -> None:
        self.terminated = True
        if not self.wait_times_out:
            self._return_code = 0

    def wait(self, timeout: float | None = None) -> int:
        if self.wait_times_out and not self.killed:
            raise subprocess.TimeoutExpired("arecord", timeout)
        if self._return_code is None:
            self._return_code = 0
        return self._return_code

    def kill(self) -> None:
        self.killed = True
        self._return_code = -9


def test_simulated_source_scripts_frames_idle_reads_and_failures() -> None:
    clock_values = iter((10_000, 20_000))
    frame = simulated_six_channel_frame((0.1, 0.0, 0.0, 0.0, 0.0, -0.1))
    source = SimulatedCaptureSource(
        (frame, None, OSError("scripted capture failure")),
        clock_ns=lambda: next(clock_values),
    )

    source.open()
    captured = source.read()

    assert captured is not None
    assert captured.captured_at_monotonic_ns == 10_000
    assert captured.interleaved_samples == frame.interleaved_samples
    assert source.read() is None
    with pytest.raises(OSError, match="scripted capture failure"):
        source.read()
    source.close()

    assert source.open_count == 1
    assert source.close_count == 1
    assert source.read_count == 3
    assert source.is_open is False


def test_simulated_frame_rejects_incomplete_six_channel_interleaving() -> None:
    with pytest.raises(ValueError, match="complete six-channel frames"):
        simulated_six_channel_frame((0.1, 0.2, 0.3))


def test_alsa_config_uses_explicit_device_and_exact_octo_geometry() -> None:
    config = AlsaCaptureConfig(
        device="hw:CARD=audioinjectoroc,DEV=0",
        period_frames=256,
        sample_format=AlsaPcmFormat.S16_LE,
    )

    assert config.period_bytes == 256 * 6 * 2
    assert config.command() == (
        "arecord",
        "--quiet",
        "--device",
        "hw:CARD=audioinjectoroc,DEV=0",
        "--file-type",
        "raw",
        "--format",
        "S16_LE",
        "--rate",
        "48000",
        "--channels",
        "6",
        "--period-size",
        "256",
        "-",
    )


def test_pcm_decoder_normalizes_s16_and_s32_without_reordering() -> None:
    s16_values = (-32_768, -16_384, 0, 16_384, 32_767, 8_192)
    s16_payload = struct.pack("<6h", *s16_values)
    decoded_s16 = decode_interleaved_pcm(
        s16_payload,
        sample_format=AlsaPcmFormat.S16_LE,
    )

    assert decoded_s16 == pytest.approx((-1.0, -0.5, 0.0, 0.5, 32_767 / 32_768, 0.25))

    s32_values = (
        -2_147_483_648,
        -1_073_741_824,
        0,
        1_073_741_824,
        2_147_483_647,
        536_870_912,
    )
    s32_payload = struct.pack("<6i", *s32_values)
    decoded_s32 = decode_interleaved_pcm(
        s32_payload,
        sample_format=AlsaPcmFormat.S32_LE,
    )

    assert decoded_s32 == pytest.approx(
        (-1.0, -0.5, 0.0, 0.5, 2_147_483_647 / 2_147_483_648, 0.25)
    )


def test_alsa_source_reads_one_complete_six_channel_period() -> None:
    values = (
        -32_768,
        -16_384,
        0,
        16_384,
        32_767,
        8_192,
        8_192,
        32_767,
        16_384,
        0,
        -16_384,
        -32_768,
    )
    process = FakeProcess(struct.pack("<12h", *values))
    commands: list[tuple[str, ...]] = []

    def factory(command: object) -> FakeProcess:
        commands.append(tuple(command))  # type: ignore[arg-type]
        return process

    source = AlsaOctoCaptureSource(
        AlsaCaptureConfig(
            device="plughw:CARD=audioinjectoroc,DEV=0",
            period_frames=2,
            sample_format=AlsaPcmFormat.S16_LE,
        ),
        muted_channels=frozenset({5}),
        process_factory=factory,
        clock_ns=lambda: 55_000,
    )

    source.open()
    frame = source.read()
    source.close()

    assert commands == [source.command]
    assert len(frame.interleaved_samples) == 12
    assert frame.interleaved_samples[:6] == pytest.approx(
        (-1.0, -0.5, 0.0, 0.5, 32_767 / 32_768, 0.25)
    )
    assert frame.captured_at_monotonic_ns == 55_000
    assert frame.sample_rate_hz == 48_000
    assert frame.muted_channels == frozenset({5})
    assert process.terminated is True
    assert source.is_open is False


def test_alsa_source_refuses_partial_period_and_reports_process_error() -> None:
    process = FakeProcess(
        struct.pack("<6h", *(0,) * 6),
        exit_when_exhausted=1,
        stderr=b"device disconnected\n",
    )
    source = AlsaOctoCaptureSource(
        AlsaCaptureConfig(
            device="hw:CARD=audioinjectoroc,DEV=0",
            period_frames=2,
            sample_format=AlsaPcmFormat.S16_LE,
        ),
        process_factory=lambda _command: process,
    )

    source.open()
    with pytest.raises(AlsaCaptureError, match="12 of 24 bytes: device disconnected"):
        source.read()
    source.close()


def test_alsa_close_kills_capture_process_after_bounded_timeout() -> None:
    process = FakeProcess(b"", wait_times_out=True)
    source = AlsaOctoCaptureSource(
        AlsaCaptureConfig(device="hw:CARD=audioinjectoroc,DEV=0"),
        process_factory=lambda _command: process,
        terminate_timeout_seconds=0.1,
    )

    source.open()
    source.close()

    assert process.terminated is True
    assert process.killed is True
