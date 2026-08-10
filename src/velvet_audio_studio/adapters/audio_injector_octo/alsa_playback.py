"""Persistent ALSA playback sink for one interleaved Audio Injector Octo PCM."""

from __future__ import annotations

from dataclasses import dataclass
import subprocess
from typing import BinaryIO, Callable, Protocol, Sequence

from velvet_audio_studio.adapters.alsa.pcm_format import AlsaPcmFormat


class AlsaPlaybackError(RuntimeError):
    pass


@dataclass(frozen=True)
class AlsaPlaybackConfig:
    """Exact Octo playback settings used to construct the ``aplay`` process."""

    device: str
    sample_rate_hz: int = 48_000
    channels: int = 8
    period_frames: int = 480
    sample_format: AlsaPcmFormat = AlsaPcmFormat.S32_LE
    aplay_binary: str = "aplay"

    def __post_init__(self) -> None:
        if not self.device.strip():
            raise ValueError("ALSA device identity cannot be empty")
        if self.sample_rate_hz <= 0:
            raise ValueError("sample_rate_hz must be positive")
        if self.channels != 8:
            raise ValueError("Audio Injector Octo playback must use exactly eight channels")
        if self.period_frames <= 0:
            raise ValueError("period_frames must be positive")
        if not self.aplay_binary.strip():
            raise ValueError("aplay_binary cannot be empty")

    @property
    def frame_bytes(self) -> int:
        return self.channels * self.sample_format.bytes_per_sample

    @property
    def period_bytes(self) -> int:
        return self.period_frames * self.frame_bytes

    def command(self) -> tuple[str, ...]:
        return (
            self.aplay_binary,
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


class PlaybackProcess(Protocol):
    stdin: BinaryIO | None
    stderr: BinaryIO | None

    def poll(self) -> int | None:
        ...

    def terminate(self) -> None:
        ...

    def wait(self, timeout: float | None = None) -> int:
        ...

    def kill(self) -> None:
        ...


ProcessFactory = Callable[[Sequence[str]], PlaybackProcess]


def _spawn_aplay(command: Sequence[str]) -> PlaybackProcess:
    return subprocess.Popen(
        tuple(command),
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        bufsize=0,
    )


class AlsaOctoPlaybackSink:
    """Single-owner eight-channel ``aplay`` stream for Audio Studio."""

    def __init__(
        self,
        config: AlsaPlaybackConfig,
        *,
        process_factory: ProcessFactory = _spawn_aplay,
        terminate_timeout_seconds: float = 1.0,
    ) -> None:
        if terminate_timeout_seconds <= 0:
            raise ValueError("terminate_timeout_seconds must be positive")
        self.config = config
        self.process_factory = process_factory
        self.terminate_timeout_seconds = terminate_timeout_seconds
        self._process: PlaybackProcess | None = None
        self._written_frames = 0

    @property
    def is_open(self) -> bool:
        return self._process is not None

    @property
    def command(self) -> tuple[str, ...]:
        return self.config.command()

    @property
    def sample_rate_hz(self) -> int:
        return self.config.sample_rate_hz

    @property
    def channels(self) -> int:
        return self.config.channels

    @property
    def sample_format(self) -> AlsaPcmFormat:
        return self.config.sample_format

    @property
    def period_frames(self) -> int:
        return self.config.period_frames

    @property
    def written_frames(self) -> int:
        return self._written_frames

    def open(self) -> None:
        if self._process is not None:
            return
        try:
            process = self.process_factory(self.command)
        except FileNotFoundError as exc:
            raise AlsaPlaybackError(
                f"ALSA playback command not found: {self.config.aplay_binary}"
            ) from exc
        except OSError as exc:
            raise AlsaPlaybackError(f"failed to start ALSA playback: {exc}") from exc

        if process.stdin is None:
            self._stop_process(process)
            raise AlsaPlaybackError("ALSA playback process did not provide a PCM stdin stream")
        return_code = process.poll()
        if return_code is not None:
            detail = self._process_error_detail(process)
            self._stop_process(process)
            raise AlsaPlaybackError(
                f"ALSA playback exited during startup with code {return_code}{detail}"
            )
        self._process = process

    def write(self, payload: bytes) -> int:
        if not payload:
            return 0
        if len(payload) % self.config.frame_bytes:
            raise AlsaPlaybackError(
                f"PCM payload contains a partial frame: {len(payload)} bytes for "
                f"{self.config.channels} channels of {self.config.sample_format.value}"
            )
        process = self._process
        if process is None:
            self.open()
            process = self._process
        assert process is not None
        assert process.stdin is not None

        return_code = process.poll()
        if return_code is not None:
            detail = self._process_error_detail(process)
            raise AlsaPlaybackError(
                f"ALSA playback exited with code {return_code}{detail}"
            )

        try:
            view = memoryview(payload)
            written = 0
            while written < len(view):
                count = process.stdin.write(view[written:])
                if count is None or count <= 0:
                    raise AlsaPlaybackError("ALSA playback stdin accepted no PCM data")
                written += count
            process.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            return_code = process.poll()
            detail = self._process_error_detail(process)
            code_text = "" if return_code is None else f" with code {return_code}"
            raise AlsaPlaybackError(
                f"ALSA playback stream failed{code_text}{detail}: {exc}"
            ) from exc

        frames = len(payload) // self.config.frame_bytes
        self._written_frames += frames
        return frames

    def close(self) -> None:
        process = self._process
        self._process = None
        if process is None:
            return
        self._stop_process(process)

    def _stop_process(self, process: PlaybackProcess) -> None:
        if process.stdin is not None:
            try:
                process.stdin.close()
            except OSError:
                pass
        if process.poll() is None:
            try:
                process.wait(timeout=self.terminate_timeout_seconds)
            except subprocess.TimeoutExpired:
                process.terminate()
                try:
                    process.wait(timeout=self.terminate_timeout_seconds)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=self.terminate_timeout_seconds)
        if process.stderr is not None:
            try:
                process.stderr.close()
            except OSError:
                pass

    @staticmethod
    def _process_error_detail(process: PlaybackProcess) -> str:
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
