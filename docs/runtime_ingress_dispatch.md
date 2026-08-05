# Durable Runtime ingress dispatch

The Runtime receiver and the Runtime dispatcher are separate trust stages.

An HTTP ingress receipt proves that Runtime durably stored the canonical Event Protocol envelope. It does not mean Court approved the event, routing completed, or an organ performed a side effect.

The dispatch layer claims accepted envelopes from the same SQLite database, hands them to Court and routing, and marks them processed only after a durable downstream receipt returns.

## Durable identities

Every accepted envelope has three related identities:

- `Idempotency-Key`: SHA-256 of the canonical Event Protocol envelope.
- `runtime-receipt-*`: proof that ingress durably accepted the envelope.
- `runtime-dispatch-*`: stable identity used for every Court and routing attempt.

The dispatch ID is derived from the ingress receipt and never changes across retries, worker restarts, database migration, or expired leases.

Downstream handlers must deduplicate by `dispatch_id`. This closes the unavoidable crash window where a handler may finish its own durable work but the dispatcher process dies before recording the returned receipt.

## Ordering rule

The oldest unprocessed event is the dispatch gate.

- If it is pending, a worker may claim it.
- If it has a live claim, every later event waits.
- If its claim expired, another worker may reclaim the same event.
- Only after it is marked processed may the next event be claimed.

This prevents a later `audio.capture.recovered` event from passing an earlier `audio.capture.degraded` event.

## Claim leases

A claim contains:

- stable dispatch ID
- ingress receipt ID
- unique claim token
- worker ID
- claim time
- lease expiration
- attempt count
- canonical envelope

Workers may renew an active lease. Completion is rejected after expiration, even when no other worker has reclaimed the event yet. A stale worker therefore cannot overwrite a newer claim.

Failed handlers release their claim back to `pending` and preserve a bounded error message. The next attempt receives the same dispatch ID and an incremented attempt count.

## Processing states

`pending`
: Accepted by ingress and waiting at the dispatch gate.

`claimed`
: Temporarily leased to one worker. A live oldest claim blocks later events.

`processed`
: Court denial or approved routing returned a durable receipt and the dispatcher committed it.

The database also preserves the last failure, processed time, final downstream receipt, and total attempts.

## Neutral handler contract

A Runtime ingress adapter implements:

```python
class RuntimeIngressHandler(Protocol):
    def dispatch(
        self,
        envelope: EventProtocolEnvelope,
        *,
        dispatch_id: str,
        ingress_receipt_id: str,
    ) -> str:
        ...
```

The returned string must be a non-empty durable receipt. Empty acknowledgements are failures and leave the event retryable.

The handler must treat `dispatch_id` as an idempotency key. The same ID may be presented again after timeout, process death, lease expiration, or loss of the dispatcher's final SQLite commit.

## Court before routing

`CourtRoutedIngressHandler` implements the neutral handler contract while preserving Velvet's authority boundary.

Court receives the canonical envelope, stable dispatch ID, and ingress receipt. It must return a durable `CourtDecision`.

Approved decisions require:

- a Court receipt
- a bounded capability
- optional approval reason

The router receives the same dispatch identity, ingress receipt, Court receipt, and capability. The event is marked processed only after routing or the destination organ returns a durable receipt.

Denied decisions require:

- a Court denial receipt
- a denial reason
- no capability

A durable denial is a completed outcome, not a transport failure. The dispatcher records the Court denial receipt and advances to the next event without calling the router.

Court exceptions, missing capabilities, invalid decisions, and empty route receipts leave the event retryable.

## Receipt chain

A completed approved event has evidence at each boundary:

1. ingress receipt: canonical envelope durably accepted
2. Court receipt: authority decision durably recorded
3. route or organ receipt: approved dispatch durably handled

A denied event ends at step 2 with its durable Court denial receipt.

The dispatch table stores the final receipt. Court and routing implementations remain responsible for preserving their own detailed receipt ledgers and linking them through `dispatch_id`.

## Failure evidence

The dispatcher distinguishes:

- `idle`: no event is currently claimable
- `processed`: final downstream receipt committed
- `retry`: handler failed and the claim returned to pending
- `claim_lost`: the worker returned after its lease or authority expired

A `claim_lost` result after a downstream receipt does not mean downstream work failed. It means the dispatcher could not safely prove completion. The event will be retried with the same dispatch ID, and downstream deduplication must return the existing receipt.

## Migration behavior

Opening an older acknowledgement database creates dispatch state for every existing ingress row. Those rows begin as `pending`, retain their original ingress receipts, and receive dispatch IDs derived by the same rule used for new events.

## Current boundary

The durable queue, lease model, strict ordering, migration path, and Court/router adapter are implemented. The repository does not grant physical authority or provide a substitute Court policy. Final Velvet Runtime integration must supply the real Court gate, capability vocabulary, routing policy, and organ receipt adapters behind these contracts.
