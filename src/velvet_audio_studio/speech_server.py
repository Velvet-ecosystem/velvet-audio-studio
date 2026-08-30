"""Dedicated Runtime -> Audio Studio speech-expression service."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Sequence

from velvet_audio_studio.runtime.http_receiver import build_runtime_receiver_server
from velvet_audio_studio.runtime.publisher_factory import build_runtime_publisher
from velvet_audio_studio.runtime.shutdown_signals import ShutdownSignalLatch
from velvet_audio_studio.runtime.speech_ingress_service import (
    build_speech_ingress_components,
)
from velvet_audio_studio.service_config import load_audio_service_config
from velvet_audio_studio.speech_output_assembly import build_speech_output_service


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Serve durable Runtime speech expressions into Audio Studio playback."
    )
    parser.add_argument("--config", required=True, help="Audio Studio YAML configuration")
    parser.add_argument(
        "--database",
        default="speech-ingress.sqlite3",
        help="SQLite file for acknowledgement, dispatch, and speech-attempt state",
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
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.poll_seconds <= 0:
        raise ValueError("poll-seconds must be positive")
    if args.max_dispatch_per_tick <= 0:
        raise ValueError("max-dispatch-per-tick must be positive")

    config = load_audio_service_config(Path(args.config))
    publisher = build_runtime_publisher(config.network)
    output = build_speech_output_service(config, publisher)
    components = build_speech_ingress_components(
        Path(args.database),
        output.speech_output_service,
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
    server.timeout = args.poll_seconds
    latch = ShutdownSignalLatch()

    try:
        with latch.installed():
            while not latch.is_requested():
                server.handle_request()
                components.dispatcher.drain_available(
                    max_events=args.max_dispatch_per_tick
                )
    finally:
        server.server_close()
        output.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
