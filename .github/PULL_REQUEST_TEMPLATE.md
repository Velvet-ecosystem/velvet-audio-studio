## Summary

Describe the change and the existing Studio boundary it reuses or extends.

## Conflict sweep

- [ ] I checked for an existing owner, contract, adapter, or implementation before adding a new path.
- [ ] This change does not duplicate booking, routing, retry, receipt, speech, or hardware-access machinery.

## Boundaries

- [ ] Audio hardware remains owned by Audio Studio.
- [ ] Language/wording ownership remains outside Audio Studio.
- [ ] No new command or actuation authority is introduced by audio priority or speech processing.
- [ ] Runtime/Event Protocol/Receipts boundaries remain explicit.

## Privacy and failure posture

- [ ] No private audio/transcript/model path/token is added to routine evidence.
- [ ] Failure and degraded behavior is documented or tested.

## Validation

- [ ] Tests pass.
- [ ] Hardware claims are backed by physical evidence, or are clearly marked as pending hardware acceptance.
