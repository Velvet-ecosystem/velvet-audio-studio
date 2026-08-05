"""Neutral Court and routing boundary for durable Runtime ingress dispatch."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from velvet_audio_studio.runtime.event_protocol import EventProtocolEnvelope


class CourtDisposition(StrEnum):
    APPROVED = "approved"
    DENIED = "denied"


@dataclass(frozen=True)
class CourtDecision:
    disposition: CourtDisposition
    court_receipt_id: str
    capability: str | None = None
    reason: str | None = None

    def __post_init__(self) -> None:
        receipt = _nonempty(self.court_receipt_id, "court_receipt_id")
        object.__setattr__(self, "court_receipt_id", receipt)
        if self.disposition is CourtDisposition.APPROVED:
            capability = _nonempty(self.capability, "approved Court capability")
            object.__setattr__(self, "capability", capability)
            if self.reason is not None and not self.reason.strip():
                raise ValueError("Court approval reason cannot be empty when provided")
        else:
            reason = _nonempty(self.reason, "Court denial reason")
            object.__setattr__(self, "reason", reason)
            if self.capability is not None:
                raise ValueError("denied Court decisions cannot grant a capability")

    @property
    def approved(self) -> bool:
        return self.disposition is CourtDisposition.APPROVED


class RuntimeCourtGate(Protocol):
    """Durably decide whether one accepted event may enter Runtime routing."""

    def decide(
        self,
        envelope: EventProtocolEnvelope,
        *,
        dispatch_id: str,
        ingress_receipt_id: str,
    ) -> CourtDecision:
        ...


class RuntimeEventRouter(Protocol):
    """Route one Court-approved event under the granted capability."""

    def route(
        self,
        envelope: EventProtocolEnvelope,
        *,
        dispatch_id: str,
        ingress_receipt_id: str,
        court_receipt_id: str,
        capability: str,
    ) -> str:
        """Return a durable route or organ receipt."""
        ...


class CourtRoutedIngressHandler:
    """RuntimeIngressHandler that places Court before every route."""

    def __init__(
        self,
        court: RuntimeCourtGate,
        router: RuntimeEventRouter,
    ) -> None:
        self.court = court
        self.router = router

    def dispatch(
        self,
        envelope: EventProtocolEnvelope,
        *,
        dispatch_id: str,
        ingress_receipt_id: str,
    ) -> str:
        stable_dispatch_id = _nonempty(dispatch_id, "dispatch_id")
        ingress_receipt = _nonempty(ingress_receipt_id, "ingress_receipt_id")
        decision = self.court.decide(
            envelope,
            dispatch_id=stable_dispatch_id,
            ingress_receipt_id=ingress_receipt,
        )
        if not isinstance(decision, CourtDecision):
            raise TypeError("Runtime Court gate must return CourtDecision")

        if not decision.approved:
            return decision.court_receipt_id

        assert decision.capability is not None
        route_receipt = self.router.route(
            envelope,
            dispatch_id=stable_dispatch_id,
            ingress_receipt_id=ingress_receipt,
            court_receipt_id=decision.court_receipt_id,
            capability=decision.capability,
        )
        return _nonempty(route_receipt, "route receipt")


def _nonempty(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value.strip()
