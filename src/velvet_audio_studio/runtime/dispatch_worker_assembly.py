"""Assembly boundary for the durable Court-routed Runtime dispatch worker."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from time import monotonic_ns, sleep, time_ns
from typing import Callable

from velvet_audio_studio.runtime.court_routing import (
    CourtRoutedIngressHandler,
    RuntimeCourtGate,
    RuntimeEventRouter,
)
from velvet_audio_studio.runtime.dispatch_quarantine import (
    QuarantinableIngressDispatchQueue,
)
from velvet_audio_studio.runtime.dispatch_worker import (
    DispatchBackoffPolicy,
    DispatchWorkerEventSink,
    PoisonEventClassifier,
    ExplicitPermanentFailureClassifier,
    RuntimeDispatchWorker,
)


@dataclass(frozen=True)
class RuntimeDispatchWorkerAssembly:
    database_path: Path
    queue: QuarantinableIngressDispatchQueue
    handler: CourtRoutedIngressHandler
    worker: RuntimeDispatchWorker


def build_runtime_dispatch_worker(
    database_path: str | Path,
    court: RuntimeCourtGate,
    router: RuntimeEventRouter,
    *,
    worker_id: str,
    lease_seconds: float = 30.0,
    lease_heartbeat_seconds: float = 10.0,
    heartbeat_interval_seconds: float = 5.0,
    idle_poll_seconds: float = 0.1,
    backoff_policy: DispatchBackoffPolicy = DispatchBackoffPolicy(),
    poison_classifier: PoisonEventClassifier = ExplicitPermanentFailureClassifier(),
    quarantine_after_failures: int = 3,
    event_sink: DispatchWorkerEventSink | None = None,
    wall_clock_ns: Callable[[], int] = time_ns,
    monotonic_clock_ns: Callable[[], int] = monotonic_ns,
    sleeper: Callable[[float], None] = sleep,
) -> RuntimeDispatchWorkerAssembly:
    """Build the worker without inventing Court policy or routing authority."""
    path = Path(database_path).expanduser().resolve()
    queue = QuarantinableIngressDispatchQueue(
        path,
        clock_ns=wall_clock_ns,
    )
    handler = CourtRoutedIngressHandler(court, router)
    worker = RuntimeDispatchWorker(
        queue,
        handler,
        worker_id=worker_id,
        lease_seconds=lease_seconds,
        lease_heartbeat_seconds=lease_heartbeat_seconds,
        heartbeat_interval_seconds=heartbeat_interval_seconds,
        idle_poll_seconds=idle_poll_seconds,
        backoff_policy=backoff_policy,
        poison_classifier=poison_classifier,
        quarantine_after_failures=quarantine_after_failures,
        event_sink=event_sink,
        clock_ns=monotonic_clock_ns,
        sleeper=sleeper,
    )
    return RuntimeDispatchWorkerAssembly(
        database_path=path,
        queue=queue,
        handler=handler,
        worker=worker,
    )
