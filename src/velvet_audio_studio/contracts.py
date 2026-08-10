"""Shared contracts for studio bookings and hardware-neutral routing."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import IntEnum
from typing import Any
from uuid import uuid4


class AudioPriority(IntEnum):
    AMBIENCE = 10
    MUSIC = 20
    MEDIA = 30
    NAVIGATION = 50
    CALL = 60
    VELVET_VOICE = 70
    SYSTEM_ALERT = 80
    SAFETY = 100


@dataclass(frozen=True, slots=True)
class StudioRequest:
    requester: str
    purpose: str
    priority: AudioPriority
    input_channels: int = 0
    output_channels: int = 0
    exclusive: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)
    preferred_output_channels: tuple[int, ...] = ()
    request_id: str = field(default_factory=lambda: str(uuid4()))
    allow_preemption: bool = False


@dataclass(frozen=True, slots=True)
class ChannelLease:
    request_id: str
    requester: str
    input_channels: tuple[int, ...]
    output_channels: tuple[int, ...]
    priority: AudioPriority
    granted_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
