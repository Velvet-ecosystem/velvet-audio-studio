"""Studio session coordination."""

from __future__ import annotations

from .channel_registry import ChannelRegistry
from .contracts import ChannelLease, StudioRequest


class StudioSessionManager:
    def __init__(self, registry: ChannelRegistry) -> None:
        self.registry = registry

    def book(self, request: StudioRequest) -> ChannelLease:
        """Grant a hardware-neutral channel lease.

        Priority ducking and preemption will be layered here once the mixer adapter
        can apply gain changes atomically and emit receipts.
        """
        return self.registry.allocate(request)

    def release(self, request_id: str) -> ChannelLease | None:
        return self.registry.release(request_id)
