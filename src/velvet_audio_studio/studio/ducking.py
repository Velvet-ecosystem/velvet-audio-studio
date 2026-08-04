"""Priority-aware ducking decisions for concurrent studio sources."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ActiveSource:
    source_id: str
    priority: int
    gain: float = 1.0


@dataclass(frozen=True)
class DuckDecision:
    source_id: str
    original_gain: float
    applied_gain: float
    ducked_by: str | None


def apply_ducking(
    sources: tuple[ActiveSource, ...],
    *,
    authority_source_id: str,
    threshold: int,
    duck_gain: float,
) -> tuple[DuckDecision, ...]:
    if not 0.0 <= duck_gain <= 1.0:
        raise ValueError("duck_gain must be between 0.0 and 1.0")

    authority = next(
        (source for source in sources if source.source_id == authority_source_id),
        None,
    )
    if authority is None:
        raise ValueError("authority source is not active")

    decisions: list[DuckDecision] = []
    for source in sources:
        should_duck = (
            source.source_id != authority_source_id
            and source.priority < threshold
            and source.priority < authority.priority
        )
        decisions.append(
            DuckDecision(
                source_id=source.source_id,
                original_gain=source.gain,
                applied_gain=source.gain * duck_gain if should_duck else source.gain,
                ducked_by=authority_source_id if should_duck else None,
            )
        )
    return tuple(decisions)
