"""Direct ALSA capture source for one interleaved Audio Injector Octo PCM."""

from __future__ import annotations

from dataclasses import dataclass
import subprocess
from struct import iter_unpack
from time import monotonic_ns
from typing import BinaryIO, Callable, Protocol, Sequence

from velvet_audio_studio.adapters.alsa.pcm_format import AlsaPcmFormat
from velvet_audio_studio.runtime.service_runner import CaptureFrame


class AlsaCaptureError(RuntimeError):
    pass


@dataclass(frozen=True)
class AlsaCaptureConfig:
    """Exact Octo capture settings used to construct the ``arecord`` process."""

    device: str
    sample_rate_hz: int = 48_000
    channels: int = 6
    period_frames: int = 480
    sample_format: AlsaPcmFormat = AlsaPcmFormat.S32_LE
    arecord_binary: str = "arecord"

    def __post_init__(self) -> None:
        if not self.device.strip():
            raise ValueError("ALSA device identity cannot be empty")
        if self.sample_rate_hz <= 0:
            raise ValueError("sample_rate_hz must be positive")
        if self.channels != 6:
            raise ValueError("Audio Injector Octo capture must use exactly six channels")
        if self.period_frames <= 0:
            raise ValueError("period_frames must be positive")
        if not self.arecord_binary.strip():
            raise ValueError("arecord_binary cannot be empty")

    @property
    def period_bytes(self) -> int:
        return self.period_frames * self.channels * self.sample_format.bytes_per_sample

    def command(self) -> tuple[str, ...]:
        return (
            self.arecord_binary,
            "--quiet",
            "--device",
            self.device,
            "--file-type",
            "raw",
            "--format",
            self.sample_format.value,
            "--rate",
            str(self.sample_rate_hz),
            "--channels",
            str(self.channels),
            "--period-size",
            str(self.period_frames),
            "-",
        )


class CaptureProcess(Protocol):
    stdout: BinaryIO | None
    stderr: BinaryIO | None

    def poll(self) -> int | None:
        ...

    def terminate(self) -> None:
        ...

    def wait(self, timeout: float | None = None) -> int:
        ...

    def kill(self) -> None:
        ...


ProcessFactory = Callable[[Sequence[str]], CaptureProcess]


def _spawn_arecord(command: Sequence[str]) -> CaptureProcess:
    return subprocess.Popen(
        tuple(command),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        bufsize=0,
    )


def decode_interleaved_pcm(
    payload: bytes,
    *,
    sample_format: AlsaPcmFormat,
    channels: int = 6,
) -> tuple[float, ...]:
    """Decode complete little-endian PCM frames into normalized floats."""
    if channels <= 0:
        raise ValueError("channels must be positive")
    bytes_per_sample = sample_format.bytes_per_sample
    frame_bytes = channels * bytes_per_sample
    if len(payload) % frame_bytes:
        raise AlsaCaptureError(
            f"PCM payload contains a partial frame: {len(payload)} bytes for "
            f"{channels} channels of {sample_format.value}"
        )

    normalizer = sample_format.normalizer
    return tuple(
        unpacked[0] / normalizer
        for unpacked in iter_unpack(sample_format.struct_format, payload)
    )


class AlsaOctoCaptureSource:
    """CaptureSource using a single six-channel ``arecord`` stdout stream."""

    def __init__(
        self,
        config: AlsaCaptureConfig,
        *,
        muted_channels: frozenset[int] = frozenset(),
        process_factory: ProcessFactory = _spawn_arecord,
        clock_ns: Callable[[], int] = monotonic_ns,
        terminate_timeout_seconds: float = 1.0,
    ) -> None:
        if terminate_timeout_seconds <= 0:
            raise ValueError("terminate_timeout_seconds must be positive")
        if any(channel < 0 or channel >= config.channels for channel in muted_channels):
            raise ValueError("muted channel index is outside the Octo capture range")
        self.config = config
        self.muted_channels = muted_channels
        self.process_factory = process_factory
        self.clock_ns = clock_ns
        self.terminate_timeout_seconds = terminate_timeout_seconds
        self._process: CaptureProcess | None = None

    @property
    def is_open(self) -> bool:
        return self._process is not None

    @property
    def command(self) -> tuple[str, ...]:
        return self.config.command()

    def open(self) -> None:
        if self._process is not None:
            raise RuntimeError("ALSA Octo capture source is already open")
        try:
            process = self.process_factory(self.command)
        except FileNotFoundError as exc:
            raise AlsaCaptureError(
                f"ALSA capture command not found: {self.config.arecord_binary}"
            ) from exc
        except OSError as exc:
            raise AlsaCaptureError(f"failed to start ALSA capture: {exc}") from exc

        if process.stdout is None:
            self._stop_process(process)
            raise AlsaCaptureError("ALSA capture process did not provide a PCM stdout stream")
        return_code = process.poll()
        if return_code is not None:
            detail = self._process_error_detail(process)
            self._stop_process(process)
            raise AlsaCaptureError(
                f"ALSA capture exited during startup with code {return_code}{detail}"
            )
        self._process = process

    def read(self) -> CaptureFrame:
        process = self._process
        if process is None or process.stdout is None:
            raise RuntimeError("ALSA Octo capture source is closed")

        payload = self._read_exact(process, self.config.period_bytes)
        samples = decode_interleaved_pcm(
            payload,
            sample_format=self.config.sample_format,
            channels=self.config.channels,
        )
        return CaptureFrame(
            interleaved_samples=samples,
            captured_at_monotonic_ns=self.clock_ns(),
            sample_rate_hz=self.config.sample_rate_hz,
            muted_channels=self.muted_channels,
        )

    def close(self) -> None:
        process = self._process
        self._process = None
        if process is None:
            return
        self._stop_process(process)

    def _read_exact(self, process: CaptureProcess, size: int) -> bytes:
        assert process.stdout is not None
        chunks: list[bytes] = []
        remaining = size
        while remaining:
            chunk = process.stdout.read(remaining)
            if chunk:
                chunks.append(chunk)
                remaining -= len(chunk)
                continue

            return_code = process.poll()
            detail = self._process_error_detail(process)
            received = size - remaining
            if return_code is None:
                raise AlsaCaptureError(
                    f"ALSA capture stream ended after {received} of {size} bytes"
                )
            raise AlsaCaptureError(
                f"ALSA capture exited with code {return_code} after "
                f"{received} of {size} bytes{detail}"
            )
        return b"".join(chunks)

    def _stop_process(self, process: CaptureProcess) -> None:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=self.terminate_timeout_seconds)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=self.terminate_timeout_seconds)
        for stream in (process.stdout, process.stderr):
            if stream is not None:
                try:
                    stream.close()
                except OSError:
                    pass

    @staticmethod
    def _process_error_detail(process: CaptureProcess) -> str:
        if process.stderr is None or process.poll() is None:
            return ""
        try:
            raw = process.stderr.read()
        except OSError:
            return ""
        if not raw:
            return ""
        text = raw.decode("utf-8", errors="replace").strip()
        return f": {text}" if text else ""
