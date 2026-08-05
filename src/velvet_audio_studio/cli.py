from __future__ import annotations

import argparse
from dataclasses import replace
import json
import sys

from velvet_audio_studio.adapters.alsa.capability_probe import probe_pcm
from velvet_audio_studio.adapters.audio_injector_octo.capture_factory import (
    OctoCaptureUnavailable,
)
from velvet_audio_studio.diagnostics.probe import probe_json
from velvet_audio_studio.runtime.acknowledgement_store import (
    AcknowledgementStoreError,
    SqliteAcknowledgementStore,
)
from velvet_audio_studio.runtime.http_receiver import (
    EventProtocolReceiver,
    build_runtime_receiver_server,
)
from velvet_audio_studio.runtime.publisher_factory import build_runtime_publisher
from velvet_audio_studio.runtime.retry_journal import RetryJournalError
from velvet_audio_studio.runtime.shutdown_signals import ShutdownSignalLatch
from velvet_audio_studio.service_assembly import build_audio_service
from velvet_audio_studio.service_config import (
    AudioServiceConfig,
    AudioServiceConfigError,
    load_audio_service_config,
)


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "probe":
            print(probe_json())
            return 0
        if args.command == "probe-pcm":
            print(probe_pcm(args.device, direction=args.direction).to_json())
            return 0
        if args.command == "validate-config":
            config = load_audio_service_config(args.config)
            if args.source is not None:
                config = config.with_capture_source(args.source)
            print(json.dumps(_config_summary(config), indent=2, sort_keys=True))
            return 0
        if args.command == "run":
            return _run_service(args)
        if args.command == "serve-runtime":
            return _serve_runtime(args)
    except (
        AcknowledgementStoreError,
        AudioServiceConfigError,
        OctoCaptureUnavailable,
        RetryJournalError,
    ) as exc:
        print(f"velvet-audio: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("velvet-audio: shutdown requested", file=sys.stderr)
        return 130
    return 2


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="velvet-audio")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("probe", help="Inspect host and Audio Injector Octo presence")

    pcm_parser = subparsers.add_parser("probe-pcm", help="Inspect ALSA PCM capabilities")
    pcm_parser.add_argument(
        "--device",
        required=True,
        help="ALSA device, for example hw:CARD=audioinjectoroc,DEV=0",
    )
    pcm_parser.add_argument(
        "--direction",
        required=True,
        choices=("playback", "capture"),
    )

    validate_parser = subparsers.add_parser(
        "validate-config",
        help="Parse and validate service YAML without opening hardware",
    )
    _add_config_arguments(validate_parser)

    run_parser = subparsers.add_parser(
        "run",
        help="Assemble and run the durable audio capture service",
    )
    _add_config_arguments(run_parser)
    run_parser.add_argument(
        "--runtime-mode",
        choices=("configured", "stdout", "unavailable"),
        default="configured",
        help=(
            "configured follows network.event_protocol_transport; stdout emits JSONL; "
            "unavailable deliberately retains events in the retry journal"
        ),
    )
    run_parser.add_argument(
        "--max-iterations",
        type=_nonnegative_integer,
        default=None,
        help="Stop after this many capture polls; omitted means run until signalled",
    )
    run_parser.add_argument(
        "--plan",
        action="store_true",
        help="Resolve the configured source and print the assembly plan without booting",
    )

    receiver_parser = subparsers.add_parser(
        "serve-runtime",
        help="Run the reference Runtime Event Protocol receiver",
    )
    receiver_parser.add_argument("--host", default="127.0.0.1")
    receiver_parser.add_argument("--port", type=_port_integer, default=8765)
    receiver_parser.add_argument(
        "--database",
        default="runtime-acknowledgements.sqlite3",
        help="SQLite acknowledgement database path",
    )
    receiver_parser.add_argument("--path", default="/v1/events")
    receiver_parser.add_argument("--health-path", default="/health")
    receiver_parser.add_argument(
        "--max-request-bytes",
        type=_positive_integer,
        default=1_048_576,
    )
    receiver_parser.add_argument(
        "--bearer-token-file",
        default=None,
        help="Optional file containing the accepted bearer token",
    )
    receiver_parser.add_argument(
        "--poll-interval",
        type=_positive_float,
        default=0.25,
        help="Signal-check interval while waiting for HTTP requests",
    )
    return parser


def _add_config_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--config",
        default="config/studio.example.yaml",
        help="Path to the audio service YAML configuration",
    )
    parser.add_argument(
        "--source",
        choices=("simulated", "alsa_octo"),
        default=None,
        help="Override capture.source from the YAML file",
    )


def _run_service(args: argparse.Namespace) -> int:
    config = load_audio_service_config(args.config)
    if args.source is not None:
        config = config.with_capture_source(args.source)

    network = config.network
    if args.runtime_mode != "configured":
        network = replace(network, event_protocol_transport=args.runtime_mode)
    publisher = build_runtime_publisher(network, stream=sys.stdout)

    assembly = build_audio_service(config, publisher)
    if args.plan:
        plan = assembly.describe()
        plan["effective_event_protocol_transport"] = network.event_protocol_transport
        print(json.dumps(plan, indent=2, sort_keys=True, default=str))
        return 0

    latch = ShutdownSignalLatch()
    with latch.installed():
        result = assembly.runner.run(
            stop_requested=latch.is_requested,
            max_iterations=args.max_iterations,
        )
    summary = {
        "node_id": config.studio.node_id,
        "iterations": len(result.iterations),
        "captured_packets": assembly.runner.status.captured_packets,
        "capture_failures": assembly.runner.status.capture_failures,
        "pending_runtime_events": assembly.runner.status.pending_runtime_events,
        "service_state": assembly.runner.status.state.value,
        "shutdown_signal": latch.signal_number,
        "retry_journal": str(config.capture.retry_journal),
    }
    print(json.dumps(summary, sort_keys=True), file=sys.stderr)
    return 0


def _serve_runtime(args: argparse.Namespace) -> int:
    store = SqliteAcknowledgementStore(args.database)
    receiver = EventProtocolReceiver(
        store,
        endpoint_path=args.path,
        health_path=args.health_path,
        max_request_bytes=args.max_request_bytes,
        bearer_token_file=args.bearer_token_file,
    )
    server = build_runtime_receiver_server(args.host, args.port, receiver)
    server.timeout = args.poll_interval
    bound_host, bound_port = server.server_address[:2]
    print(
        json.dumps(
            {
                "status": "listening",
                "host": bound_host,
                "port": bound_port,
                "event_path": receiver.endpoint_path,
                "health_path": receiver.health_path,
                "database": str(store.path),
                "bearer_token_required": receiver.bearer_token_file is not None,
            },
            sort_keys=True,
        ),
        file=sys.stderr,
    )

    latch = ShutdownSignalLatch()
    try:
        with latch.installed():
            while not latch.is_requested():
                server.handle_request()
    finally:
        server.server_close()
    print(
        json.dumps(
            {
                "status": "stopped",
                "shutdown_signal": latch.signal_number,
                "accepted_events": store.count(),
            },
            sort_keys=True,
        ),
        file=sys.stderr,
    )
    return 0


def _config_summary(config: AudioServiceConfig) -> dict[str, object]:
    return {
        "config_path": str(config.config_path),
        "node_id": config.studio.node_id,
        "host_adapter": config.studio.host_adapter,
        "hardware_adapter": config.studio.hardware_adapter,
        "input_channels": config.studio.input_channels,
        "output_channels": config.studio.output_channels,
        "capture_source": config.capture.source,
        "sample_rate_hz": config.capture.sample_rate_hz,
        "sample_format": config.capture.sample_format.value,
        "period_frames": config.capture.period_frames,
        "heartbeat_interval_ms": config.capture.heartbeat_interval_ms,
        "retry_journal": str(config.capture.retry_journal),
        "network_transport": config.network.transport,
        "event_protocol_transport": config.network.event_protocol_transport,
        "runtime_endpoint": config.network.runtime_endpoint,
        "request_timeout_seconds": config.network.request_timeout_seconds,
        "bearer_token_file": (
            str(config.network.bearer_token_file)
            if config.network.bearer_token_file is not None
            else None
        ),
        "max_response_bytes": config.network.max_response_bytes,
    }


def _nonnegative_integer(value: str) -> int:
    parsed = int(value)
    if parsed < 0:
        raise argparse.ArgumentTypeError("value must be non-negative")
    return parsed


def _positive_integer(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("value must be positive")
    return parsed


def _positive_float(value: str) -> float:
    parsed = float(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("value must be positive")
    return parsed


def _port_integer(value: str) -> int:
    parsed = int(value)
    if parsed < 0 or parsed > 65_535:
        raise argparse.ArgumentTypeError("port must be in the range 0 to 65535")
    return parsed


if __name__ == "__main__":
    raise SystemExit(main())
