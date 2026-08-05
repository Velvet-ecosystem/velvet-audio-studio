from __future__ import annotations

from pathlib import Path
import signal

import pytest

from velvet_audio_studio.runtime.event_protocol import EventProtocolPublisher
from velvet_audio_studio.runtime.http_transport import HttpEventProtocolTransport
from velvet_audio_studio.runtime.local_transport import (
    JsonlEventProtocolTransport,
    UnavailableRuntimePublisher,
)
from velvet_audio_studio.runtime.publisher_factory import build_runtime_publisher
from velvet_audio_studio.runtime.shutdown_signals import ShutdownSignalLatch
from velvet_audio_studio.service_config import (
    AudioServiceConfigError,
    load_audio_service_config,
)


def _write_config(
    tmp_path: Path,
    *,
    event_transport: str = "http_json",
    endpoint: str | None = "http://runtime.local:8765/v1/events",
    token_file: str | None = "secrets/runtime.token",
) -> Path:
    endpoint_yaml = "null" if endpoint is None else endpoint
    token_yaml = "null" if token_file is None else token_file
    path = tmp_path / "studio.yaml"
    path.write_text(
        f"""studio:
  node_id: velvet-audio-test
  host_adapter: raspberry_pi_3
  hardware_adapter: audio_injector_octo
  input_channels: 6
  output_channels: 8

capture:
  source: simulated
  identity_terms: null
  sample_rate_hz: 48000
  sample_format: S32_LE
  period_frames: 480
  heartbeat_interval_ms: 5000
  idle_poll_seconds: 0
  retry_journal: state/runtime-retry.jsonl
  max_pending_runtime_events: 64
  backlog_warning_ratio: 0.75
  backlog_max_age_ms: 30000

network:
  transport: ethernet
  event_protocol_transport: {event_transport}
  runtime_endpoint: {endpoint_yaml}
  request_timeout_seconds: 1.25
  bearer_token_file: {token_yaml}
  max_response_bytes: 4096
""",
        encoding="utf-8",
    )
    return path


def test_http_network_configuration_resolves_token_path(tmp_path: Path) -> None:
    config = load_audio_service_config(_write_config(tmp_path))

    assert config.network.transport == "ethernet"
    assert config.network.event_protocol_transport == "http_json"
    assert config.network.runtime_endpoint == "http://runtime.local:8765/v1/events"
    assert config.network.request_timeout_seconds == 1.25
    assert config.network.bearer_token_file == (
        tmp_path / "secrets/runtime.token"
    ).resolve()
    assert config.network.max_response_bytes == 4096


def test_http_transport_requires_endpoint(tmp_path: Path) -> None:
    path = _write_config(tmp_path, endpoint=None)

    with pytest.raises(AudioServiceConfigError, match="runtime_endpoint"):
        load_audio_service_config(path)


def test_runtime_endpoint_rejects_credentials(tmp_path: Path) -> None:
    path = _write_config(
        tmp_path,
        endpoint="http://user:secret@runtime.local/v1/events",
    )

    with pytest.raises(AudioServiceConfigError, match="embedded credentials"):
        load_audio_service_config(path)


def test_publisher_factory_builds_http_event_protocol_publisher(tmp_path: Path) -> None:
    config = load_audio_service_config(_write_config(tmp_path))

    publisher = build_runtime_publisher(config.network)

    assert isinstance(publisher, EventProtocolPublisher)
    assert isinstance(publisher.transport, HttpEventProtocolTransport)
    assert publisher.transport.settings.endpoint == config.network.runtime_endpoint
    assert publisher.transport.settings.timeout_seconds == 1.25
    assert publisher.transport.settings.bearer_token_file == (
        tmp_path / "secrets/runtime.token"
    ).resolve()


def test_publisher_factory_preserves_local_and_offline_modes(tmp_path: Path) -> None:
    stdout_config = load_audio_service_config(
        _write_config(
            tmp_path,
            event_transport="stdout",
            endpoint=None,
            token_file=None,
        )
    )
    unavailable_config = load_audio_service_config(
        _write_config(
            tmp_path,
            event_transport="unavailable",
            endpoint=None,
            token_file=None,
        )
    )

    stdout_publisher = build_runtime_publisher(stdout_config.network)
    unavailable_publisher = build_runtime_publisher(unavailable_config.network)

    assert isinstance(stdout_publisher, EventProtocolPublisher)
    assert isinstance(stdout_publisher.transport, JsonlEventProtocolTransport)
    assert isinstance(unavailable_publisher, UnavailableRuntimePublisher)


def test_shutdown_latch_marks_signal_and_restores_handlers() -> None:
    previous_int = signal.getsignal(signal.SIGINT)
    previous_term = signal.getsignal(signal.SIGTERM)
    latch = ShutdownSignalLatch()

    with latch.installed():
        latch.request(signal.SIGTERM)
        assert latch.is_requested() is True
        assert latch.signal_number == signal.SIGTERM

    assert signal.getsignal(signal.SIGINT) == previous_int
    assert signal.getsignal(signal.SIGTERM) == previous_term


def test_systemd_unit_preserves_alsa_and_orders_shutdown() -> None:
    unit = Path("packaging/systemd/velvet-audio.service").read_text(encoding="utf-8")

    assert "ExecStartPre=" in unit
    assert "validate-config --config /etc/velvet-audio/studio.yaml" in unit
    assert "--runtime-mode configured" in unit
    assert "SupplementaryGroups=audio" in unit
    assert "StateDirectory=velvet-audio" in unit
    assert "KillSignal=SIGTERM" in unit
    assert "RestartPreventExitStatus=2" in unit
    assert "PrivateDevices=true" not in unit
    assert "RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6" in unit


def test_runtime_receiver_unit_isolated_and_durable() -> None:
    unit = Path("packaging/systemd/velvet-runtime-receiver.service").read_text(
        encoding="utf-8"
    )

    assert "velvet-audio serve-runtime" in unit
    assert "--database /var/lib/velvet-runtime-receiver/acknowledgements.sqlite3" in unit
    assert "--bearer-token-file /etc/velvet-runtime-receiver/runtime.token" in unit
    assert "StateDirectory=velvet-runtime-receiver" in unit
    assert "PrivateDevices=true" in unit
    assert "KillSignal=SIGTERM" in unit
    assert "RestartPreventExitStatus=2" in unit
    assert "RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6" in unit
    assert "SupplementaryGroups=audio" not in unit
