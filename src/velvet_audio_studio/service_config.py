"""Typed configuration for assembling the Velvet audio service."""

from __future__ import annotations

from dataclasses import dataclass, replace
import os
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlsplit

import yaml

from velvet_audio_studio.adapters.audio_injector_octo import AlsaPcmFormat


class AudioServiceConfigError(ValueError):
    pass


@dataclass(frozen=True)
class StudioIdentityConfig:
    node_id: str
    host_adapter: str
    hardware_adapter: str
    input_channels: int
    output_channels: int


@dataclass(frozen=True)
class CaptureServiceConfig:
    source: str
    identity_terms: tuple[str, ...]
    pcm_device: int
    use_plughw: bool
    sample_rate_hz: int
    sample_format: AlsaPcmFormat
    period_frames: int
    heartbeat_interval_ms: int
    idle_poll_seconds: float
    retry_journal: Path
    max_pending_runtime_events: int
    backlog_warning_ratio: float
    backlog_max_age_ms: int


@dataclass(frozen=True)
class NetworkServiceConfig:
    transport: str
    event_protocol_transport: str
    runtime_endpoint: str | None
    request_timeout_seconds: float
    bearer_token_file: Path | None
    max_response_bytes: int


@dataclass(frozen=True)
class AudioServiceConfig:
    studio: StudioIdentityConfig
    capture: CaptureServiceConfig
    network: NetworkServiceConfig
    config_path: Path

    def with_capture_source(self, source: str) -> AudioServiceConfig:
        normalized = _capture_source(source)
        capture = replace(self.capture, source=normalized)
        _validate_capture(self.studio, capture)
        return replace(self, capture=capture)


def load_audio_service_config(path: str | Path) -> AudioServiceConfig:
    config_path = Path(path).expanduser().resolve()
    try:
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise AudioServiceConfigError(f"audio service config was not found: {config_path}") from exc
    except OSError as exc:
        raise AudioServiceConfigError(f"audio service config could not be read: {exc}") from exc
    except yaml.YAMLError as exc:
        raise AudioServiceConfigError(f"audio service config is invalid YAML: {exc}") from exc

    root = _mapping(raw, "configuration root")
    studio_raw = _mapping(root.get("studio"), "studio")
    capture_raw = _mapping(root.get("capture"), "capture")
    network_raw = _mapping(root.get("network", {}), "network")

    studio = StudioIdentityConfig(
        node_id=_nonempty_text(studio_raw.get("node_id"), "studio.node_id"),
        host_adapter=_nonempty_text(
            studio_raw.get("host_adapter", "raspberry_pi_3"),
            "studio.host_adapter",
        ),
        hardware_adapter=_nonempty_text(
            studio_raw.get("hardware_adapter", "audio_injector_octo"),
            "studio.hardware_adapter",
        ),
        input_channels=_positive_int(
            studio_raw.get("input_channels", 6),
            "studio.input_channels",
        ),
        output_channels=_positive_int(
            studio_raw.get("output_channels", 8),
            "studio.output_channels",
        ),
    )

    source = _capture_source(capture_raw.get("source", "alsa_octo"))
    identity_terms_raw = capture_raw.get("identity_terms", ("audioinjector", "octo"))
    if identity_terms_raw is None and source == "simulated":
        identity_terms_raw = ()
    if not isinstance(identity_terms_raw, (list, tuple)):
        raise AudioServiceConfigError("capture.identity_terms must be a list of strings")
    identity_terms = tuple(
        _nonempty_text(term, f"capture.identity_terms[{index}]")
        for index, term in enumerate(identity_terms_raw)
    )

    format_text = _nonempty_text(
        capture_raw.get("sample_format", AlsaPcmFormat.S32_LE.value),
        "capture.sample_format",
    )
    try:
        sample_format = AlsaPcmFormat(format_text)
    except ValueError as exc:
        supported = ", ".join(member.value for member in AlsaPcmFormat)
        raise AudioServiceConfigError(
            f"capture.sample_format must be one of: {supported}"
        ) from exc

    journal_path = _resolved_path(
        capture_raw.get("retry_journal", "runtime-retry.jsonl"),
        "capture.retry_journal",
        config_path=config_path,
    )

    capture = CaptureServiceConfig(
        source=source,
        identity_terms=identity_terms,
        pcm_device=_nonnegative_int(capture_raw.get("pcm_device", 0), "capture.pcm_device"),
        use_plughw=_boolean(capture_raw.get("use_plughw", False), "capture.use_plughw"),
        sample_rate_hz=_positive_int(
            capture_raw.get("sample_rate_hz", 48_000),
            "capture.sample_rate_hz",
        ),
        sample_format=sample_format,
        period_frames=_positive_int(
            capture_raw.get("period_frames", 480),
            "capture.period_frames",
        ),
        heartbeat_interval_ms=_positive_int(
            capture_raw.get("heartbeat_interval_ms", 5_000),
            "capture.heartbeat_interval_ms",
        ),
        idle_poll_seconds=_nonnegative_float(
            capture_raw.get("idle_poll_seconds", 0.01),
            "capture.idle_poll_seconds",
        ),
        retry_journal=journal_path,
        max_pending_runtime_events=_positive_int(
            capture_raw.get("max_pending_runtime_events", 1_024),
            "capture.max_pending_runtime_events",
        ),
        backlog_warning_ratio=_ratio(
            capture_raw.get("backlog_warning_ratio", 0.75),
            "capture.backlog_warning_ratio",
        ),
        backlog_max_age_ms=_positive_int(
            capture_raw.get("backlog_max_age_ms", 30_000),
            "capture.backlog_max_age_ms",
        ),
    )

    endpoint_raw = network_raw.get("runtime_endpoint")
    endpoint = None if endpoint_raw is None else _nonempty_text(
        endpoint_raw,
        "network.runtime_endpoint",
    )
    event_transport_default = "http_json" if endpoint is not None else "stdout"
    token_raw = network_raw.get("bearer_token_file")
    token_path = None if token_raw is None else _resolved_path(
        token_raw,
        "network.bearer_token_file",
        config_path=config_path,
    )
    network = NetworkServiceConfig(
        transport=_nonempty_text(network_raw.get("transport", "ethernet"), "network.transport"),
        event_protocol_transport=_event_protocol_transport(
            network_raw.get("event_protocol_transport", event_transport_default)
        ),
        runtime_endpoint=endpoint,
        request_timeout_seconds=_positive_float(
            network_raw.get("request_timeout_seconds", 2.0),
            "network.request_timeout_seconds",
        ),
        bearer_token_file=token_path,
        max_response_bytes=_positive_int(
            network_raw.get("max_response_bytes", 65_536),
            "network.max_response_bytes",
        ),
    )

    _validate_capture(studio, capture)
    _validate_network(network)
    return AudioServiceConfig(
        studio=studio,
        capture=capture,
        network=network,
        config_path=config_path,
    )


def _validate_capture(
    studio: StudioIdentityConfig,
    capture: CaptureServiceConfig,
) -> None:
    if capture.source == "alsa_octo":
        if not capture.identity_terms:
            raise AudioServiceConfigError(
                "capture.identity_terms cannot be empty for alsa_octo"
            )
        if studio.input_channels != 6:
            raise AudioServiceConfigError(
                "Audio Injector Octo capture requires studio.input_channels to equal 6"
            )
        if studio.output_channels != 8:
            raise AudioServiceConfigError(
                "Audio Injector Octo playback requires studio.output_channels to equal 8"
            )
        if studio.hardware_adapter != "audio_injector_octo":
            raise AudioServiceConfigError(
                "capture.source alsa_octo requires studio.hardware_adapter audio_injector_octo"
            )


def _validate_network(network: NetworkServiceConfig) -> None:
    endpoint = network.runtime_endpoint
    if network.event_protocol_transport == "http_json" and endpoint is None:
        raise AudioServiceConfigError(
            "network.runtime_endpoint is required for event_protocol_transport http_json"
        )
    if endpoint is None:
        return
    parsed = urlsplit(endpoint)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise AudioServiceConfigError(
            "network.runtime_endpoint must be an absolute http or https URL"
        )
    if parsed.username is not None or parsed.password is not None:
        raise AudioServiceConfigError(
            "network.runtime_endpoint must not contain embedded credentials"
        )
    if parsed.fragment:
        raise AudioServiceConfigError(
            "network.runtime_endpoint must not contain a URL fragment"
        )


def _mapping(value: Any, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise AudioServiceConfigError(f"{name} must be a mapping")
    return value


def _capture_source(value: Any) -> str:
    source = _nonempty_text(value, "capture.source").casefold()
    if source not in {"simulated", "alsa_octo"}:
        raise AudioServiceConfigError(
            "capture.source must be either simulated or alsa_octo"
        )
    return source


def _event_protocol_transport(value: Any) -> str:
    transport = _nonempty_text(value, "network.event_protocol_transport").casefold()
    if transport not in {"stdout", "http_json", "unavailable"}:
        raise AudioServiceConfigError(
            "network.event_protocol_transport must be stdout, http_json, or unavailable"
        )
    return transport


def _resolved_path(value: Any, name: str, *, config_path: Path) -> Path:
    text = _nonempty_text(value, name)
    path = Path(os.path.expandvars(text)).expanduser()
    if not path.is_absolute():
        path = config_path.parent / path
    return path.resolve()


def _nonempty_text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise AudioServiceConfigError(f"{name} must be a non-empty string")
    return value.strip()


def _positive_int(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise AudioServiceConfigError(f"{name} must be a positive integer")
    return value


def _nonnegative_int(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise AudioServiceConfigError(f"{name} must be a non-negative integer")
    return value


def _positive_float(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        raise AudioServiceConfigError(f"{name} must be a positive number")
    return float(value)


def _nonnegative_float(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        raise AudioServiceConfigError(f"{name} must be a non-negative number")
    return float(value)


def _ratio(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise AudioServiceConfigError(f"{name} must be a number")
    ratio = float(value)
    if not 0 < ratio <= 1:
        raise AudioServiceConfigError(f"{name} must be in the range (0, 1]")
    return ratio


def _boolean(value: Any, name: str) -> bool:
    if not isinstance(value, bool):
        raise AudioServiceConfigError(f"{name} must be true or false")
    return value
