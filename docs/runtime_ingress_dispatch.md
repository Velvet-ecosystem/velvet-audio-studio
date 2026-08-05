# Durable Runtime ingress dispatch

The Runtime receiver and the Runtime dispatcher are separate trust stages.

An HTTP ingress receipt proves that Runtime durably stored the canonical Event Protocol envelope. It does not mean Court approved the event, routing completed, or an organ performed a side effect.

The dispatch layer claims accepted envelopes from the same SQLite database, hands them to a Court or routing adapter, and marks them processed only after that adapter returns a durable downstream receipt.

## Durable identities

Every accepted envelope has three related identities:

- `Idempotency-Key`: SHA-256 of the canonical Event Protocol envelope.
- `runtime-receipt-*`: proof that ingress durably accepted the envelope.
- `runtime-dispatch-*`: stable identity used for every Court and routing attempt.

The dispatch ID is derived from the ingress receipt and never changes across retries, worker restarts, or expired leases.

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
: Court or routing returned a durable downstream receipt and the dispatcher committed it.

The database also preserves the last failure, processed time, downstream receipt, and total attempts.

## Handler contract

A Court or routing adapter implements:

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

## Failure evidence

The dispatcher distinguishes:

- `idle`: no event is currently claimable
- `processed`: downstream receipt committed
- `retry`: handler failed and the claim returned to pending
- `claim_lost`: the worker returned after its lease or authority expired

A `claim_lost` result after a downstream receipt does not mean downstream work failed. It means the dispatcher could not safely prove completion. The event will be retried with the same dispatch ID, and downstream deduplication must return the existing receipt.

## Current boundary

`DurableIngressDispatcher` provides the durable queue and neutral Court/router contract. It does not yet grant physical authority or bypass Velvet Court. The final Runtime integration must place Court capability checks, routing policy, and organ-specific receipts behind `RuntimeIngressHandler`.
