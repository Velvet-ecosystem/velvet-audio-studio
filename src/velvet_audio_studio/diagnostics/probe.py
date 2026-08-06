"""Read-only host and Octo diagnostics.

This module intentionally performs no mixer writes and emits no audio. It is
safe to run before the physical acceptance sequence.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import platform
import shutil
import subprocess
from typing import Any

from velvet_audio_studio.adapters.alsa.card_discovery import find_card, list_alsa_cards


@dataclass(frozen=True, slots=True)
class ProbeResult:
    status: str
    kernel: str
    machine: str
    card_index: int | None
    card_id: str | None
    card_name: str | None
    playback_tool: bool
    capture_tool: bool
    degraded_reasons: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _device_list(command: str) -> str:
    tool = shutil.which(command)
    if tool is None:
        return ""
    completed = subprocess.run(
        [tool, "-l"],
        check=False,
        capture_output=True,
        text=True,
        timeout=5,
    )
    return f"{completed.stdout}\n{completed.stderr}"


def probe_octo() -> ProbeResult:
    cards = list_alsa_cards()
    card = find_card(cards)
    reasons: list[str] = []

    aplay_available = shutil.which("aplay") is not None
    arecord_available = shutil.which("arecord") is not None
    if not aplay_available:
        reasons.append("alsa_playback_tool_missing")
    if not arecord_available:
        reasons.append("alsa_capture_tool_missing")
    if card is None:
        reasons.append("audio_injector_octo_not_discovered")
    else:
        playback_listing = _device_list("aplay").casefold()
        capture_listing = _device_list("arecord").casefold()
        identity = f"{card.card_id} {card.name}".casefold()
        if aplay_available and not any(term in playback_listing for term in (card.card_id.casefold(), "audioinjector", "octo")):
            reasons.append("octo_playback_pcm_not_listed")
        if arecord_available and not any(term in capture_listing for term in (card.card_id.casefold(), "audioinjector", "octo")):
            reasons.append("octo_capture_pcm_not_listed")
        if "audioinjector" not in identity and "octo" not in identity:
            reasons.append("octo_identity_uncertain")

    return ProbeResult(
        status="ready_for_physical_test" if not reasons else "degraded",
        kernel=platform.release(),
        machine=platform.machine(),
        card_index=card.index if card else None,
        card_id=card.card_id if card else None,
        card_name=card.name if card else None,
        playback_tool=aplay_available,
        capture_tool=arecord_available,
        degraded_reasons=tuple(reasons),
    )


def probe_json(*, indent: int = 2) -> str:
    return json.dumps(probe_octo().to_dict(), indent=indent, sort_keys=True)
