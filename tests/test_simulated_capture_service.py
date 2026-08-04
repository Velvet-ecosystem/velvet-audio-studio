from pathlib import Path

from velvet_audio_studio.capture.supervisor import CaptureSupervisor
from velvet_audio_studio.runtime.backlog_supervisor import DurableBacklogSupervisor
from velvet_audio_studio.runtime.capture_pipeline import ReliablePublishedCapturePipeline
from velvet_audio_studio.runtime.durable_retry_queue import DurableOrderedRetryQueue
from velvet_audio_studio.runtime.publisher import InMemoryRuntimePublisher
from velvet_audio_studio.runtime.retry_journal import JsonlRetryJournal
from velvet_audio_studio.runtime.service_runner import ReliableAudioServiceRunner
from velvet_audio_studio.simulated.capture_source import (
    SimulatedCaptureSource,
    simulated_six_channel_frame,
)


def test_concrete_simulated_source_runs_through_full_service(tmp_path: Path) -> None:
    clock_values = iter(
        (
            1_000_000_000,
            1_010_000_000,
            1_020_000_000,
            1_030_000_000,
        )
    )
    source = SimulatedCaptureSource(
        (
            simulated_six_channel_frame(
                (
                    0.2,
                    0.1,
                    0.0,
                    0.0,
                    0.0,
                    0.0,
                    -0.2,
                    -0.1,
                    0.0,
                    0.0,
                    0.0,
                    0.0,
                ),
                captured_at_monotonic_ns=1_005_000_000,
            ),
            None,
        )
    )
    publisher = InMemoryRuntimePublisher()
    journal = JsonlRetryJournal(tmp_path / "audio-retry.jsonl")
    queue = DurableOrderedRetryQueue(journal)
    supervisor = CaptureSupervisor()
    pipeline = ReliablePublishedCapturePipeline(
        supervisor,
        publisher,
        queue,
        DurableBacklogSupervisor(queue, max_age_ms=60_000),
    )
    runner = ReliableAudioServiceRunner(
        pipeline,
        source,
        heartbeat_interval_ms=60_000,
        idle_poll_seconds=0.0,
        clock_ns=lambda: next(clock_values),
        sleeper=lambda _seconds: None,
    )

    result = runner.run(max_iterations=2)

    assert len(result.iterations) == 2
    assert result.iterations[0].capture is not None
    assert result.iterations[0].capture.capture.handoff.selected_logical_name == (
        "driver_upper_mic"
    )
    assert [event.event for event in publisher.events] == [
        "audio.service.booting",
        "audio.capture.starting",
        "audio.service.running",
        "audio.capture.packet",
        "audio.capture.active",
        "audio.voice_input.ready",
        "audio.service.stopping",
        "audio.capture.stopped",
        "audio.service.stopped",
    ]
    assert source.open_count == 1
    assert source.close_count == 1
    assert source.read_count == 2
    assert queue.status.queue.pending_count == 0
    assert journal.load() == ()
