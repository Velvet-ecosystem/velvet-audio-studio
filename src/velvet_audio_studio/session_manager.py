"""Studio session coordination."""

from __future__ import annotations

from .channel_registry import ChannelRegistry, ChannelUnavailable
from .contracts import ChannelLease, StudioRequest


class StudioSessionManager:
    def __init__(self, registry: ChannelRegistry) -> None:
        self.registry = registry

    def book(self, request: StudioRequest) -> ChannelLease:
        """Grant a hardware-neutral channel lease.

        Preemption is opt-in and limited to preferred output-only bookings. A
        request may take conflicting output leases only when every conflict has
        strictly lower priority. Equal or higher priority leases remain intact.
        """
        try:
            return self.registry.allocate(request)
        except ChannelUnavailable:
            if (
                not request.allow_preemption
                or request.input_channels != 0
                or not request.preferred_output_channels
            ):
                raise

            requested_outputs = frozenset(request.preferred_output_channels)
            conflicts = tuple(
                lease
                for lease in self.registry.leases
                if requested_outputs.intersection(lease.output_channels)
            )
            if not conflicts:
                raise
            if any(lease.priority >= request.priority for lease in conflicts):
                raise

            for lease in conflicts:
                self.registry.release(lease.request_id)
            return self.registry.allocate(request)

    def release(self, request_id: str) -> ChannelLease | None:
        return self.registry.release(request_id)
