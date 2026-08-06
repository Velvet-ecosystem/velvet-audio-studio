from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import re
import shutil
import subprocess
from typing import Callable


@dataclass(frozen=True)
class PcmCapabilities:
    device: str
    direction: str
    channels_min: int | None
    channels_max: int | None
    rates: tuple[int, ...]
    formats: tuple[str, ...]
    available: bool
    degraded_reasons: tuple[str, ...] = ()

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, sort_keys=True)


def _integers(text: str) -> tuple[int, ...]:
    return tuple(sorted({int(value) for value in re.findall(r"\b\d+\b", text)}))


def parse_hw_params(text: str, *, device: str, direction: str) -> PcmCapabilities:
    channels_match = re.search(r"CHANNELS:\s*([^\n]+)", text)
    channels = _integers(channels_match.group(1)) if channels_match else ()
    formats_match = re.search(r"FORMAT:\s*([^\n]+)", text)
    formats = tuple(formats_match.group(1).split()) if formats_match else ()
    rates_match = re.search(r"RATE:\s*([^\n]+)", text)
    rates = _integers(rates_match.group(1)) if rates_match else ()

    reasons: list[str] = []
    if not channels:
        reasons.append("channel range not reported")
    if not formats:
        reasons.append("sample formats not reported")
    if not rates:
        reasons.append("sample rates not reported")

    return PcmCapabilities(
        device=device,
        direction=direction,
        channels_min=min(channels) if channels else None,
        channels_max=max(channels) if channels else None,
        rates=rates,
        formats=formats,
        available=not reasons,
        degraded_reasons=tuple(reasons),
    )


def _unavailable(device: str, direction: str, reason: str) -> PcmCapabilities:
    return PcmCapabilities(
        device=device,
        direction=direction,
        channels_min=None,
        channels_max=None,
        rates=(),
        formats=(),
        available=False,
        degraded_reasons=(reason,),
    )


def probe_pcm(
    device: str,
    *,
    direction: str,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> PcmCapabilities:
    if direction not in {"playback", "capture"}:
        raise ValueError("direction must be playback or capture")

    executable = "aplay" if direction == "playback" else "arecord"
    if shutil.which(executable) is None:
        return _unavailable(device, direction, f"{executable} is not installed")

    sink = "/dev/zero" if direction == "playback" else "/dev/null"
    try:
        result = runner(
            [executable, "--dump-hw-params", "--device", device, sink],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
        combined = "\n".join(part for part in (result.stdout, result.stderr) if part)
    except subprocess.TimeoutExpired as error:
        stdout = error.stdout.decode() if isinstance(error.stdout, bytes) else (error.stdout or "")
        stderr = error.stderr.decode() if isinstance(error.stderr, bytes) else (error.stderr or "")
        combined = "\n".join(part for part in (stdout, stderr) if part)

    if not combined.strip():
        return _unavailable(device, direction, f"{executable} returned no capability data")
    return parse_hw_params(combined, device=device, direction=direction)
