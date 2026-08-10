"""Channel inventory and lease allocation."""

from __future__ import annotations

from .contracts import ChannelLease, StudioRequest


class ChannelUnavailable(RuntimeError):
    pass


class ChannelRegistry:
    def __init__(self, input_count: int, output_count: int) -> None:
        self._inputs = tuple(range(input_count))
        self._outputs = tuple(range(output_count))
        self._leases: dict[str, ChannelLease] = {}

    @property
    def leases(self) -> tuple[ChannelLease, ...]:
        return tuple(self._leases.values())

    def allocate(self, request: StudioRequest) -> ChannelLease:
        used_inputs = {channel for lease in self._leases.values() for channel in lease.input_channels}
        used_outputs = {channel for lease in self._leases.values() for channel in lease.output_channels}
        free_inputs = tuple(channel for channel in self._inputs if channel not in used_inputs)
        free_outputs = tuple(channel for channel in self._outputs if channel not in used_outputs)

        if len(free_inputs) < request.input_channels:
            raise ChannelUnavailable(f"Insufficient channels for {request.requester}: {request.purpose}")

        if request.preferred_output_channels:
            preferred = request.preferred_output_channels
            if request.output_channels != len(preferred):
                raise ChannelUnavailable(
                    "preferred output count must match requested output_channels"
                )
            if len(set(preferred)) != len(preferred):
                raise ChannelUnavailable("preferred output channels must be unique")
            if any(channel not in self._outputs for channel in preferred):
                raise ChannelUnavailable("preferred output channel is outside the registry")
            if any(channel in used_outputs for channel in preferred):
                raise ChannelUnavailable("preferred output channel is already leased")
            selected_outputs = preferred
        else:
            if len(free_outputs) < request.output_channels:
                raise ChannelUnavailable(
                    f"Insufficient channels for {request.requester}: {request.purpose}"
                )
            selected_outputs = free_outputs[: request.output_channels]

        lease = ChannelLease(
            request_id=request.request_id,
            requester=request.requester,
            input_channels=free_inputs[: request.input_channels],
            output_channels=selected_outputs,
            priority=request.priority,
        )
        self._leases[request.request_id] = lease
        return lease

    def release(self, request_id: str) -> ChannelLease | None:
        return self._leases.pop(request_id, None)
