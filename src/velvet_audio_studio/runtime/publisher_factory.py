"""Construct Runtime publishers from validated audio service configuration."""

from __future__ import annotations

import sys
from typing import TextIO

from velvet_audio_studio.runtime.event_protocol import EventProtocolPublisher
from velvet_audio_studio.runtime.http_transport import HttpEventProtocolTransport
from velvet_audio_studio.runtime.local_transport import (
    JsonlEventProtocolTransport,
    UnavailableRuntimePublisher,
)
from velvet_audio_studio.runtime.publisher import RuntimeEventPublisher
from velvet_audio_studio.service_config import NetworkServiceConfig


def build_runtime_publisher(
    network: NetworkServiceConfig,
    *,
    stream: TextIO | None = None,
) -> RuntimeEventPublisher:
    """Build the configured Event Protocol publisher without touching capture hardware."""
    mode = network.event_protocol_transport
    if mode == "stdout":
        return EventProtocolPublisher(
            JsonlEventProtocolTransport(sys.stdout if stream is None else stream)
        )
    if mode == "unavailable":
        return UnavailableRuntimePublisher(
            "Velvet Runtime is deliberately configured as unavailable"
        )
    if mode == "http_json":
        endpoint = network.runtime_endpoint
        if endpoint is None:
            raise ValueError("HTTP Event Protocol transport requires a Runtime endpoint")
        transport = HttpEventProtocolTransport(
            endpoint,
            timeout_seconds=network.request_timeout_seconds,
            bearer_token_file=network.bearer_token_file,
            max_response_bytes=network.max_response_bytes,
        )
        return EventProtocolPublisher(transport)
    raise ValueError(f"unsupported Event Protocol transport: {mode}")
