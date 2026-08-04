from __future__ import annotations

from dataclasses import asdict
import json
from pathlib import Path
from typing import Iterable

from velvet_audio_studio.capture.supervisor import RuntimeAudioEvent


class RetryJournalError(RuntimeError):
    pass


class JsonlRetryJournal:
    """Durable JSON-lines journal for ordered Runtime events.

    The file is rewritten atomically after acknowledgement so a process restart
    restores only events that were not confirmed downstream.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def load(self) -> tuple[RuntimeAudioEvent, ...]:
        if not self.path.exists():
            return ()

        events: list[RuntimeAudioEvent] = []
        for line_number, raw_line in enumerate(self.path.read_text(encoding="utf-8").splitlines(), start=1):
            if not raw_line.strip():
                continue
            try:
                data = json.loads(raw_line)
                event = RuntimeAudioEvent(
                    event=str(data["event"]),
                    source_id=str(data["source_id"]),
                    occurred_at_monotonic_ns=int(data["occurred_at_monotonic_ns"]),
                    packet_sequence=int(data["packet_sequence"]),
                    payload=dict(data["payload"]),
                )
            except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
                raise RetryJournalError(
                    f"invalid retry journal entry at line {line_number}: {exc}"
                ) from exc
            events.append(event)
        return tuple(events)

    def replace(self, events: Iterable[RuntimeAudioEvent]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        lines = [json.dumps(asdict(event), sort_keys=True, separators=(",", ":")) for event in events]
        payload = "\n".join(lines)
        if payload:
            payload += "\n"
        temporary.write_text(payload, encoding="utf-8")
        temporary.replace(self.path)

    def clear(self) -> None:
        self.replace(())
