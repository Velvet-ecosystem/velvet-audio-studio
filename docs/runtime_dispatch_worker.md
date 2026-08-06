# Runtime dispatch worker

The Runtime dispatch worker continuously consumes the durable ingress lane after HTTP acknowledgement. It does not replace Court, routing, or organ authority. It keeps the oldest accepted event moving through those boundaries with leases, receipts, retry evidence, and explicit quarantine.

## Assembly boundary

Runtime supplies its real Court and router implementations:

```python
assembly = build_runtime_dispatch_worker(
    "/var/lib/velvet-runtime-receiver/acknowledgements.sqlite3",
    court,
    router,
    worker_id="runtime-dispatch-01",
)

assembly.worker.run(stop_requested=shutdown_latch.is_requested)
```

The assembler creates:

- `QuarantinableIngressDispatchQueue`
- `CourtRoutedIngressHandler`
- `RuntimeDispatchWorker`

It does not provide an allow-all Court, mock capability grant, or placeholder route receipt.

## Ordered lane

The oldest unprocessed event remains the gate.

A worker may process the next event only when the gate is:

- pending and successfully claimed
- already processed
- explicitly quarantined with a durable quarantine receipt

A live claim blocks all later events. An expired claim may be reclaimed with the same stable dispatch ID.

## Lease heartbeats

Court or routing work may take longer than one initial lease. The worker invokes the handler on a dedicated thread and renews the SQLite claim while it remains active.

Each successful renewal emits:

```text
runtime.dispatch.lease_renewed
```

A lease heartbeat proves only that the worker still owns dispatch authority for that ingress row. It does not claim that Court approved the event or that an organ completed work.

If renewal fails, the worker no longer commits the result. A late downstream receipt is reported through `runtime.dispatch.claim_lost`. The event is later retried with the same dispatch ID so Court and the router can return their existing deduplicated receipt.

## Retry backoff

Failures use bounded exponential backoff. Successful processing or quarantine resets the consecutive-failure counter.

Default delays begin at 250 milliseconds, double after each consecutive failure, and stop growing at 8 seconds. Idle polling uses its own interval and does not count as failure.

The worker distinguishes:

- `processed`
- `retry`
- `quarantined`
- `claim_lost`
- `error`
- `idle`

Infrastructure errors, Court outages, missing routes, and temporary organ failures remain retryable.

## Poison-event quarantine

There is deliberately no rule that says “skip an event after N failures.” Repetition alone does not prove the event is bad. Three identical failures during a Court outage are still a Court outage.

The default classifier considers an event poison-eligible only when the handler explicitly raises:

```python
PermanentDispatchError("unsupported fixed event schema")
```

The worker then requires the same failure fingerprint to occur repeatedly. A different failure resets the consecutive-evidence count.

At the configured threshold, quarantine is committed atomically:

- the failure fingerprint and bounded reason are preserved
- a stable `runtime-quarantine-*` receipt is created
- the ingress dispatch row becomes terminal
- the next ordered event may advance

Quarantine is not a successful route. Its receipt proves that Runtime deliberately isolated the event with evidence instead of silently dropping it.

## Health events

The worker emits observational health events:

- `runtime.dispatch.worker.started`
- `runtime.dispatch.worker.heartbeat`
- `runtime.dispatch.claimed`
- `runtime.dispatch.lease_renewed`
- `runtime.dispatch.processed`
- `runtime.dispatch.retry`
- `runtime.dispatch.quarantined`
- `runtime.dispatch.claim_lost`
- `runtime.dispatch.worker.error`
- `runtime.dispatch.worker.stopping`
- `runtime.dispatch.worker.stopped`

Heartbeat payloads include queue counts, expired claims, active dispatch ID, retries, quarantine count, and infrastructure errors.

The health sink is not authoritative. Sink failure is counted and ignored so a broken dashboard cannot stop Court or alter dispatch state.

## Shutdown behavior

A stop request prevents the worker from claiming new events and interrupts idle or backoff sleeps.

An already-running Court or route call is allowed to finish while lease renewal continues. Python threads cannot be safely killed without risking half-completed side effects. Once the handler returns, the worker either commits the downstream receipt or reports lost authority.

The final production service should pair the worker with `ShutdownSignalLatch` and a systemd timeout appropriate for the longest permitted Court or route operation.

## Evidence tables

The shared SQLite database contains:

- `event_acknowledgements`
- `event_dispatch_state`
- `event_dispatch_failure_evidence`
- `event_dispatch_quarantine`

Ingress acceptance, Court decision, route completion, retry evidence, and quarantine remain separate facts. No single vague success flag replaces those receipts.
