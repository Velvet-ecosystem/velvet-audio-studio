from velvet_audio_studio.capture.supervisor import CaptureSupervisor
from velvet_audio_studio.runtime.capture_pipeline import PublishedCapturePipeline
from velvet_audio_studio.runtime.publisher import (
    AudioRuntimeBridge,
    InMemoryRuntimePublisher,
)


def test_pipeline_processes_and_publishes_ordered_capture_events() -> None:
    publisher = InMemoryRuntimePublisher()
    supervisor = CaptureSupervisor()
    supervisor.start(occurred_at_monotonic_ns=1_000_000_000)
    pipeline = PublishedCapturePipeline(
        supervisor,
        AudioRuntimeBridge(publisher),
    )

    result = pipeline.process_and_publish(
        (
            0.2, 0.1, 0.0, 0.0, 0.0, 0.0,
            -0.2, -0.1, 0.0, 0.0, 0.0, 0.0,
        ),
        captured_at_monotonic_ns=1_010_000_000,
        observed_at_monotonic_ns=1_020_000_000,
    )

    expected_names = [
        "audio.capture.packet",
        "audio.capture.active",
        "audio.voice_input.ready",
    ]
    assert [event.event for event in result.capture.events] == expected_names
    assert [event.event for event in publisher.events] == expected_names
    assert result.delivery.delivered_count == 3
    assert result.delivery.failed_count == 0
    assert result.capture.handoff.selected_logical_name == "driver_upper_mic"


def test_pipeline_returns_capture_even_when_runtime_delivery_fails() -> None:
    class FailingPublisher:
        def publish(self, event):
            raise OSError("runtime socket unavailable")

    supervisor = CaptureSupervisor()
    supervisor.start(occurred_at_monotonic_ns=1_000_000_000)
    pipeline = PublishedCapturePipeline(
        supervisor,
        AudioRuntimeBridge(FailingPublisher()),
    )

    result = pipeline.process_and_publish(
        (0.2, 0.0, 0.0, 0.0, 0.0, 0.0),
        captured_at_monotonic_ns=1_010_000_000,
        observed_at_monotonic_ns=1_020_000_000,
    )

    assert result.capture.handoff.event == "audio.voice_input.ready"
    assert result.delivery.degraded is True
    assert result.delivery.failed_count == len(result.capture.events)
