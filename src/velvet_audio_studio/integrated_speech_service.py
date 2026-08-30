"""Run Audio Studio capture and Runtime speech ingress in one hardware-owning process."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Sequence

from velvet_audio_studio.runtime.http_receiver import build_runtime_receiver_server
from velvet_audio_studio.runtime.publisher_factory import build_runtime_publisher
from velvet_audio_studio.runtime.shutdown_signals import ShutdownSignalLatch
from velvet_audio_studio.runtime.speech_ingress_background import (
    SpeechIngressBackgroundRunner,
)
from velvet_audio_studio.runtime.speech_ingress_service import (
    build_speech_ingress_components,
)
from velvet_audio_studio.service_assembly import build_audio_service
from velvet_audio_studio.service_config import load_audio_service_config


class IntegratedSpeechServiceError(RuntimeError):
    """Raised when the single-owner capture/speech service cannot be assembled."""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run the normal Audio Studio service and durable Runtime speech ingress "
            "inside one process so the Audio Injector playback sink has one owner."
        )
    )
    parser.add_argument("--config", required=True)
    parser.add_argument(
        "--database",
        default="/var/lib/velvet-audio/speech-ingress.sqlite3",
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--path", default="/v1/speech-expressions")
    parser.add_argument("--health-path", default="/health")
    parser.add_argument("--bearer-token-file", default=None)
    parser.add_argument("--worker-id", default="audio-speech-ingress")
    parser.add_argument("--lease-seconds", type=float, default=30.0)
    parser.add_argument("--poll-seconds", type=float, default=0.25)
    parser.add_argument("--max-request-bytes", type=int, default=65_536)
    parser.add_argument("--max-dispatch-per-tick", type=int, default=16)
    parser.add_argument("--stop-timeout-seconds", type=float, default=5.0)
    parser.add_argument(
        "--max-iterations",
        type=int,
        default=None,
        help="Test/bench limit for the primary capture loop",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    _validate_args(args)

    config = load_audio_service_config(Path(args.config))
    publisher = build_runtime_publisher(config.network, stream=sys.stdout)
    assembly = build_audio_service(config, publisher)
    if assembly.speech_output_service is None:
        assembly.close_output()
        raise IntegratedSpeechServiceError(
            "integrated Runtime speech ingress requires TTS and playback to be enabled"
        )

    components = build_speech_ingress_components(
        Path(args.database),
        assembly.speech_output_service,
        endpoint_path=args.path,
        health_path=args.health_path,
        max_request_bytes=args.max_request_bytes,
        bearer_token_file=(
            None if args.bearer_token_file is None else Path(args.bearer_token_file)
        ),
        worker_id=args.worker_id,
        lease_seconds=args.lease_seconds,
    )
    server = build_runtime_receiver_server(args.host, args.port, components.receiver)
    background = SpeechIngressBackgroundRunner(
        server,
        components.dispatcher,
        poll_seconds=args.poll_seconds,
        max_dispatch_per_tick=args.max_dispatch_per_tick,
    )
    bound_host, bound_port = server.server_address[:2]
    print(
        json.dumps(
            {
                "status": "starting",
                "node_id": config.studio.node_id,
                "speech_ingress_host": bound_host,
                "speech_ingress_port": bound_port,
                "speech_ingress_path": components.receiver.endpoint_path,
                "speech_ingress_database": str(components.store.path),
                "bearer_token_required": (
                    components.receiver.bearer_token_file is not None
                ),
                "single_playback_owner": True,
            },
            sort_keys=True,
        ),
        file=sys.stderr,
    )

    latch = ShutdownSignalLatch()
    result = None
    background.start()
    try:
        with latch.installed():
            result = assembly.runner.run(
                stop_requested=lambda: latch.is_requested() or background.has_failed,
                max_iterations=args.max_iterations,
            )
    finally:
        try:
            background.stop(timeout_seconds=args.stop_timeout_seconds)
        finally:
            assembly.close_output()

    background.raise_if_failed()
    print(
        json.dumps(
            {
                "status": "stopped",
                "node_id": config.studio.node_id,
                "iterations": len(result.iterations) if result is not None else 0,
                "captured_packets": assembly.runner.status.captured_packets,
                "capture_failures": assembly.runner.status.capture_failures,
                "speech_ingress_pending": components.queue.stats().pending,
                "speech_ingress_processed": components.queue.stats().processed,
                "shutdown_signal": latch.signal_number,
            },
            sort_keys=True,
        ),
        file=sys.stderr,
    )
    return 0


def _validate_args(args: argparse.Namespace) -> None:
    if args.port < 0 or args.port > 65_535:
        raise ValueError("port must be in the range 0 to 65535")
    if args.poll_seconds <= 0:
        raise ValueError("poll-seconds must be positive")
    if args.max_request_bytes <= 0:
        raise ValueError("max-request-bytes must be positive")
    if args.max_dispatch_per_tick <= 0:
        raise ValueError("max-dispatch-per-tick must be positive")
    if args.lease_seconds <= 0:
        raise ValueError("lease-seconds must be positive")
    if args.stop_timeout_seconds <= 0:
        raise ValueError("stop-timeout-seconds must be positive")
    if args.max_iterations is not None and args.max_iterations < 0:
        raise ValueError("max-iterations cannot be negative")
    if not _is_loopback_host(args.host) and args.bearer_token_file is None:
        raise IntegratedSpeechServiceError(
            "non-loopback speech ingress requires --bearer-token-file"
        )


def _is_loopback_host(host: str) -> bool:
    return host.strip().casefold() in {"127.0.0.1", "localhost", "::1"}


if __name__ == "__main__":
    raise SystemExit(main())
