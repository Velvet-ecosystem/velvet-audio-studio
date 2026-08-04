from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Sequence

from velvet_audio_studio.capture.supervisor import RuntimeAudioEvent


SUMMARIZABLE_EVENTS = frozenset({"audio.capture.packet"})
CRITICAL_EVENTS = frozenset(
    {
        "audio.capture.starting",
        "audio.capture.active",
        "audio.capture.degraded",
        "audio.capture.recovered",
        "audio.capture.stopped",
        "audio.voice_input.ready",
        "audio.voice_input.degraded",
        "audio.runtime_backlog.warning",
        "audio.runtime_backlog.critical",
        "audio.runtime_backlog.recovered",
        "audio.runtime_backlog.compacted",
    }
)


@dataclass(frozen=True)
class BacklogHealth:
    state: str
    pending_count: int
    capacity_ratio: float
    oldest_age_ms: float | None
    warning_reasons: tuple[str, ...]


@dataclass(frozen=True)
class CompactionResult:
    events: tuple[RuntimeAudioEvent, ...]
    removed_count: int
    summary_count: int


def assess_backlog(
    events: Sequence[RuntimeAudioEvent],
    *,
    max_pending: int,
    observed_at_monotonic_ns: int,
    capacity_warning_ratio: float = 0.75,
    max_age_ms: int = 30_000,
) -> BacklogHealth:
    if max_pending <= 0:
        raise ValueError("max_pending must be positive")
    if not 0 < capacity_warning_ratio <= 1:
        raise ValueError("capacity_warning_ratio must be in the range (0, 1]")
    if max_age_ms <= 0:
        raise ValueError("max_age_ms must be positive")

    pending_count = len(events)
    ratio = pending_count / max_pending
    oldest_age_ms = None
    if events:
        oldest_ns = min(event.occurred_at_monotonic_ns for event in events)
        oldest_age_ms = max(0.0, (observed_at_monotonic_ns - oldest_ns) / 1_000_000)

    reasons: list[str] = []
    if ratio >= 1:
        reasons.append("retry queue at capacity")
    elif ratio >= capacity_warning_ratio:
        reasons.append("retry queue nearing capacity")
    if oldest_age_ms is not None and oldest_age_ms > max_age_ms:
        reasons.append("retry queue contains over-age events")

    state = "critical" if ratio >= 1 or (oldest_age_ms or 0) > max_age_ms * 2 else "warning" if reasons else "healthy"
    return BacklogHealth(state, pending_count, ratio, oldest_age_ms, tuple(reasons))


def compact_backlog(events: Sequence[RuntimeAudioEvent]) -> CompactionResult:
    """Collapse only consecutive capture packets within the same sequence region.

    Lifecycle, voice-handoff, and backlog-health events are never removed,
    reordered, or merged. A packet run becomes one summary event located at the
    first packet's position.
    """
    compacted: list[RuntimeAudioEvent] = []
    removed = 0
    summaries = 0
    index = 0

    while index < len(events):
        event = events[index]
        if event.event not in SUMMARIZABLE_EVENTS:
            compacted.append(event)
            index += 1
            continue

        run = [event]
        cursor = index + 1
        while cursor < len(events) and events[cursor].event in SUMMARIZABLE_EVENTS:
            run.append(events[cursor])
            cursor += 1

        if len(run) == 1:
            compacted.append(event)
        else:
            first = run[0]
            last = run[-1]
            payload = dict(last.payload)
            payload.update(
                {
                    "summary": True,
                    "summarized_event": "audio.capture.packet",
                    "summarized_count": len(run),
                    "first_packet_sequence": first.packet_sequence,
                    "last_packet_sequence": last.packet_sequence,
                    "first_occurred_at_monotonic_ns": first.occurred_at_monotonic_ns,
                    "last_occurred_at_monotonic_ns": last.occurred_at_monotonic_ns,
                }
            )
            compacted.append(
                replace(
                    first,
                    event="audio.capture.packet.summary",
                    packet_sequence=last.packet_sequence,
                    payload=payload,
                )
            )
            removed += len(run) - 1
            summaries += 1
        index = cursor

    return CompactionResult(tuple(compacted), removed, summaries)
