from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import Sequence

from velvet_audio_studio.voice.vad import VoiceActivityDecision


@dataclass(frozen=True)
class UtteranceCaptureConfig:
    pre_roll_ms: int = 200
    minimum_duration_ms: int = 120
    maximum_duration_ms: int = 12_000

    def __post_init__(self) -> None:
        if self.pre_roll_ms < 0:
            raise ValueError("pre_roll_ms cannot be negative")
        if self.minimum_duration_ms < 0:
            raise ValueError("minimum_duration_ms cannot be negative")
        if self.maximum_duration_ms <= 0:
            raise ValueError("maximum_duration_ms must be positive")
        if self.minimum_duration_ms > self.maximum_duration_ms:
            raise ValueError(
                "minimum_duration_ms cannot exceed maximum_duration_ms"
            )


@dataclass(frozen=True)
class VoiceUtterance:
    utterance_id: str
    samples: tuple[float, ...]
    sample_rate_hz: int
    started_at_monotonic_ns: int
    ended_at_monotonic_ns: int
    selected_logical_name: str
    confidence: float
    completion_reason: str
    truncated: bool

    @property
    def duration_ms(self) -> int:
        if self.sample_rate_hz <= 0:
            return 0
        return round(len(self.samples) * 1_000 / self.sample_rate_hz)


@dataclass(frozen=True)
class UtteranceCaptureResult:
    event: str | None
    completed: VoiceUtterance | None
    active: bool
    buffered_samples: int
    reason: str | None = None


class BoundedUtteranceCapture:
    """Collect mono samples locally until silence or the duration ceiling."""

    def __init__(
        self,
        config: UtteranceCaptureConfig = UtteranceCaptureConfig(),
    ) -> None:
        self.config = config
        self._pre_roll: list[float] = []
        self._active_samples: list[float] | None = None
        self._sample_rate_hz: int | None = None
        self._started_at_ns: int | None = None
        self._selected_logical_name: str | None = None
        self._confidence = 0.0
        self._sequence = 0

    @property
    def active(self) -> bool:
        return self._active_samples is not None

    @property
    def buffered_samples(self) -> int:
        return len(self._active_samples or ())

    def process(
        self,
        samples: Sequence[float],
        decision: VoiceActivityDecision,
        *,
        sample_rate_hz: int,
        occurred_at_monotonic_ns: int,
        selected_logical_name: str,
        confidence: float,
    ) -> UtteranceCaptureResult:
        normalized = tuple(float(sample) for sample in samples)
        if sample_rate_hz <= 0:
            raise ValueError("sample_rate_hz must be positive")
        if occurred_at_monotonic_ns < 0:
            raise ValueError("occurred_at_monotonic_ns cannot be negative")
        if not isinstance(selected_logical_name, str) or not selected_logical_name.strip():
            raise ValueError("selected_logical_name must be a non-empty string")
        if not isfinite(confidence) or not 0.0 <= confidence <= 1.0:
            raise ValueError("confidence must be finite and within [0, 1]")
        if any(not isfinite(sample) for sample in normalized):
            raise ValueError("utterance samples must be finite")

        if self.active and sample_rate_hz != self._sample_rate_hz:
            raise ValueError("sample rate cannot change during an utterance")

        if decision.event == "audio.voice_activity.started" and not self.active:
            self._active_samples = list(self._pre_roll)
            self._active_samples.extend(normalized)
            self._pre_roll.clear()
            self._sample_rate_hz = sample_rate_hz
            self._started_at_ns = occurred_at_monotonic_ns
            self._selected_logical_name = selected_logical_name.strip()
            self._confidence = confidence
            return self._enforce_ceiling(occurred_at_monotonic_ns)

        if self.active:
            self._active_samples.extend(normalized)
            self._confidence = max(self._confidence, confidence)
            if decision.event == "audio.voice_activity.ended":
                return self._complete(
                    occurred_at_monotonic_ns,
                    completion_reason="silence",
                    truncated=False,
                )
            return self._enforce_ceiling(occurred_at_monotonic_ns)

        self._append_pre_roll(normalized, sample_rate_hz)
        return UtteranceCaptureResult(
            event=None,
            completed=None,
            active=False,
            buffered_samples=0,
        )

    def cancel(
        self,
        *,
        occurred_at_monotonic_ns: int,
        reason: str,
    ) -> UtteranceCaptureResult:
        if occurred_at_monotonic_ns < 0:
            raise ValueError("occurred_at_monotonic_ns cannot be negative")
        normalized_reason = reason.strip() if isinstance(reason, str) else ""
        if not normalized_reason:
            raise ValueError("cancel reason must be a non-empty string")
        was_active = self.active
        self._reset_active()
        self._pre_roll.clear()
        return UtteranceCaptureResult(
            event="audio.utterance.cancelled" if was_active else None,
            completed=None,
            active=False,
            buffered_samples=0,
            reason=normalized_reason if was_active else None,
        )

    def _append_pre_roll(
        self,
        samples: tuple[float, ...],
        sample_rate_hz: int,
    ) -> None:
        maximum = round(sample_rate_hz * self.config.pre_roll_ms / 1_000)
        if maximum <= 0:
            self._pre_roll.clear()
            return
        self._pre_roll.extend(samples)
        if len(self._pre_roll) > maximum:
            del self._pre_roll[:-maximum]

    def _enforce_ceiling(
        self,
        occurred_at_monotonic_ns: int,
    ) -> UtteranceCaptureResult:
        assert self._active_samples is not None
        assert self._sample_rate_hz is not None
        maximum = round(
            self._sample_rate_hz * self.config.maximum_duration_ms / 1_000
        )
        if len(self._active_samples) < maximum:
            return UtteranceCaptureResult(
                event=None,
                completed=None,
                active=True,
                buffered_samples=len(self._active_samples),
            )
        del self._active_samples[maximum:]
        return self._complete(
            occurred_at_monotonic_ns,
            completion_reason="maximum_duration",
            truncated=True,
        )

    def _complete(
        self,
        occurred_at_monotonic_ns: int,
        *,
        completion_reason: str,
        truncated: bool,
    ) -> UtteranceCaptureResult:
        assert self._active_samples is not None
        assert self._sample_rate_hz is not None
        assert self._started_at_ns is not None
        assert self._selected_logical_name is not None

        samples = tuple(self._active_samples)
        duration_ms = round(len(samples) * 1_000 / self._sample_rate_hz)
        if duration_ms < self.config.minimum_duration_ms:
            self._reset_active()
            return UtteranceCaptureResult(
                event="audio.utterance.discarded",
                completed=None,
                active=False,
                buffered_samples=0,
                reason="below minimum duration",
            )

        self._sequence += 1
        utterance = VoiceUtterance(
            utterance_id=f"utterance-{self._sequence:08d}",
            samples=samples,
            sample_rate_hz=self._sample_rate_hz,
            started_at_monotonic_ns=self._started_at_ns,
            ended_at_monotonic_ns=occurred_at_monotonic_ns,
            selected_logical_name=self._selected_logical_name,
            confidence=self._confidence,
            completion_reason=completion_reason,
            truncated=truncated,
        )
        self._reset_active()
        return UtteranceCaptureResult(
            event="audio.utterance.ready",
            completed=utterance,
            active=False,
            buffered_samples=0,
        )

    def _reset_active(self) -> None:
        self._active_samples = None
        self._sample_rate_hz = None
        self._started_at_ns = None
        self._selected_logical_name = None
        self._confidence = 0.0
