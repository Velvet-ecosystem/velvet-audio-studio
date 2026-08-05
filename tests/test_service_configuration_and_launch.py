from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from velvet_audio_studio.adapters.alsa.capability_probe import PcmCapabilities
from velvet_audio_studio.adapters.alsa.card_discovery import AlsaCard
from velvet_audio_studio.adapters.audio_injector_octo import (
    AlsaCaptureConfig,
    AlsaOctoCaptureSource,
    AlsaPcmFormat,
)
from velvet_audio_studio.adapters.audio_injector_octo.capture_factory import (
    OctoCaptureResolution,
)
from velvet_audio_studio.cli import main
from velvet_audio_studio.runtime.event_protocol import (
    EventProtocolEnvelope,
    EventProtocolPublisher,
)
from velvet_audio_studio.runtime.local_transport import JsonlEventProtocolTransport
from velvet_audio_studio.runtime.publisher import InMemoryRuntimePublisher
from velvet_audio_studio.service_assembly import build_audio_service
from velvet_audio_studio.service_config import (
    AudioServiceConfigError,
    load_audio_service_config,
)


class TickClock:
    def __init__(self, value: int = 1_000_000_000, step: int = 10_000_000) -> None:
        self.value = value
        self.step = step

    def __call__(self) -> int:
        current = self.value
        self.value += self.step
        return current


def _write_config(
    tmp_path: Path,
    *,
    source: str = "simulated",
    input_channels: int = 6,
    output_channels: int = 8,
    hardware_adapter: str = "audio_injector_octo",
    identity_terms: str = "  - audioinjector\n  - octo",
) -> Path:
    path = tmp_path / "studio.yaml"
    path.write_text(
        f"""studio:
  node_id: velvet-audio-test
  host_adapter: raspberry_pi_3
  hardware_adapter: {hardware_adapter}
  input_channels: {input_channels}
  output_channels: {output_channels}

capture:
  source: {source}
  identity_terms:
{identity_terms}
  pcm_device: 0
  use_plughw: false
  sample_rate_hz: 48000
  sample_format: S32_LE
  period_frames: 2
  heartbeat_interval_ms: 5000
  idle_poll_seconds: 0
  retry_journal: state/runtime-retry.jsonl
  max_pending_runtime_events: 32
  backlog_warning_ratio: 0.75
  backlog_max_age_ms: 30000

network:
  transport: ethernet
  runtime_endpoint: null
""",
        encoding="utf-8",
    )
    return path


def test_config_resolves_relative_retry_journal(tmp_path: Path) -> None:
    path = _write_config(tmp_path)

    config = load_audio_service_config(path)

    assert config.capture.source == "simulated"
    assert config.capture.retry_journal == (tmp_path / "state/runtime-retry.jsonl").resolve()
    assert config.capture.sample_format is AlsaPcmFormat.S32_LE
    assert config.capture.max_pending_runtime_events == 32


def test_invalid_octo_channel_shape_is_rejected(tmp_path: Path) -> None:
    path = _write_config(tmp_path, source="alsa_octo", input_channels=2)

    with pytest.raises(AudioServiceConfigError, match="input_channels"):
        load_audio_service_config(path)


def test_source_override_receives_octo_cross_validation(tmp_path: Path) -> None:
    path = _write_config(
        tmp_path,
        source="simulated",
        hardware_adapter="generic_usb_audio",
    )
    config = load_audio_service_config(path)

    with pytest.raises(AudioServiceConfigError, match="hardware_adapter"):
        config.with_capture_source("alsa_octo")


def test_empty_identity_terms_are_rejected_for_octo_override(tmp_path: Path) -> None:
    path = _write_config(
        tmp_path,
        source="simulated",
        identity_terms="",
    )
    config = load_audio_service_config(path)

    with pytest.raises(AudioServiceConfigError, match="identity_terms"):
        config.with_capture_source("alsa_octo")


def test_jsonl_transport_emits_canonical_envelope_and_stable_receipt() -> None:
    stream = io.StringIO()
    transport = JsonlEventProtocolTransport(stream)
    envelope = EventProtocolEnvelope(
        event_type="audio.service.running",
        source_id="audio.service",
        sequence=4,
        occurred_at_monotonic_ns=1_000_000_000,
        payload={"state": "running"},
    )

    first = transport.publish_envelope(envelope)
    second = transport.publish_envelope(envelope)

    assert first == second
    assert first.startswith("event-protocol-jsonl-")
    lines = stream.getvalue().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0]) == {
        "event_type": "audio.service.running",
        "occurred_at_monotonic_ns": 1_000_000_000,
        "payload": {"state": "running"},
        "sequence": 4,
        "source_id": "audio.service",
    }


def test_simulated_assembly_runs_full_service_and_clears_journal(tmp_path: Path) -> None:
    config = load_audio_service_config(_write_config(tmp_path))
    publisher = InMemoryRuntimePublisher()
    clock = TickClock()
    assembly = build_audio_service(
        config,
        publisher,
        clock_ns=clock,
        sleeper=lambda _seconds: None,
    )

    result = assembly.runner.run(max_iterations=1)

    assert len(result.iterations) == 1
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
    assert assembly.retry_queue.status.queue.pending_count == 0
    assert assembly.journal.load() == ()
    assert assembly.runner.status.state.value == "stopped"


def test_alsa_assembly_passes_configured_resolution_settings(tmp_path: Path) -> None:
    config = load_audio_service_config(_write_config(tmp_path, source="alsa_octo"))
    observed: dict[str, object] = {}
    card = AlsaCard(index=7, card_id="audioinjectoroc", name="AudioInjector Octo")
    alsa_config = AlsaCaptureConfig(
        device="hw:CARD=audioinjectoroc,DEV=0",
        sample_rate_hz=48_000,
        period_frames=2,
        sample_format=AlsaPcmFormat.S32_LE,
    )
    capabilities = PcmCapabilities(
        device=alsa_config.device,
        direction="capture",
        channels_min=6,
        channels_max=6,
        rates=(48_000,),
        formats=("S32_LE",),
        available=True,
    )

    def resolver(**kwargs: object) -> OctoCaptureResolution:
        observed.update(kwargs)
        return OctoCaptureResolution(
            card=card,
            config=alsa_config,
            capabilities=capabilities,
            accepted=True,
            degraded_reasons=(),
        )

    assembly = build_audio_service(
        config,
        InMemoryRuntimePublisher(),
        capture_resolver=resolver,
    )

    assert isinstance(assembly.capture_source, AlsaOctoCaptureSource)
    assert observed == {
        "identity_terms": ("audioinjector", "octo"),
        "pcm_device": 0,
        "plug": False,
        "sample_rate_hz": 48_000,
        "period_frames": 2,
        "sample_format": AlsaPcmFormat.S32_LE,
    }
    assert assembly.describe()["alsa_device"] == "hw:CARD=audioinjectoroc,DEV=0"


def test_cli_validates_config_without_opening_hardware(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = _write_config(tmp_path, source="alsa_octo")

    exit_code = main(["validate-config", "--config", str(path)])

    assert exit_code == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["capture_source"] == "alsa_octo"
    assert summary["retry_journal"].endswith("state/runtime-retry.jsonl")


def test_cli_runs_simulated_service_through_event_protocol_jsonl(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = _write_config(tmp_path, source="alsa_octo")

    exit_code = main(
        [
            "run",
            "--config",
            str(path),
            "--source",
            "simulated",
            "--runtime-mode",
            "stdout",
            "--max-iterations",
            "1",
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    envelopes = [json.loads(line) for line in captured.out.splitlines()]
    assert [envelope["event_type"] for envelope in envelopes] == [
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
    summary = json.loads(captured.err)
    assert summary["captured_packets"] == 1
    assert summary["pending_runtime_events"] == 0
    assert summary["service_state"] == "stopped"


def test_cli_unavailable_runtime_retains_ordered_service_history(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = _write_config(tmp_path)
    config = load_audio_service_config(path)

    exit_code = main(
        [
            "run",
            "--config",
            str(path),
            "--runtime-mode",
            "unavailable",
            "--max-iterations",
            "0",
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert captured.out == ""
    summary = json.loads(captured.err)
    assert summary["pending_runtime_events"] > 0
    journal_events = [
        json.loads(line)["event"]
        for line in config.capture.retry_journal.read_text(encoding="utf-8").splitlines()
    ]
    assert journal_events[:3] == [
        "audio.service.booting",
        "audio.capture.starting",
        "audio.service.running",
    ]
    assert journal_events[-3:] == [
        "audio.service.stopping",
        "audio.capture.stopped",
        "audio.service.stopped",
    ]
