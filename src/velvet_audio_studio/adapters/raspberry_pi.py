"""Raspberry Pi host discovery and process boundary."""

from __future__ import annotations

import platform
from pathlib import Path


class RaspberryPiAudioHost:
    def __init__(self, model_path: Path = Path("/proc/device-tree/model")) -> None:
        self.model_path = model_path

    def model(self) -> str:
        if not self.model_path.exists():
            return platform.machine()
        return self.model_path.read_text(encoding="utf-8", errors="replace").strip("\x00\n ")

    def is_supported(self) -> bool:
        model = self.model().lower()
        return "raspberry pi 3" in model
