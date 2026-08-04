# Hardware Acceptance

A Raspberry Pi and Audio Injector Octo node is not trusted merely because ALSA lists a card. Acceptance proves the full signal path.

## Evidence to capture

- Raspberry Pi model and revision
- operating-system image identifier
- kernel version
- boot configuration and active overlays
- ALSA card long name and stable identity
- playback and capture PCM names
- supported channel counts
- supported sample formats and rates
- mixer controls
- codec and clock status when observable
- per-channel playback result
- per-channel capture result
- loopback or known-source result
- underrun, overrun, distortion, and clock-slip observations
- final health state and degraded reason

## Required tests

1. Discover the card by identity rather than numeric card order.
2. Verify one six-channel capture endpoint and one eight-channel playback endpoint, or record the actual exposed capability.
3. Play an identifiable signal through every physical output.
4. Capture a known signal through every physical input.
5. Run concurrent capture and playback.
6. Run a sustained stream long enough to expose clock or buffer instability.
7. Reboot and repeat discovery to prove stable startup.
8. Save a receipt before marking the unit available.

## Acceptance states

- `ACCEPTED`: all required tests pass.
- `DEGRADED`: usable with explicit missing channels or restrictions.
- `REJECTED`: enumeration succeeds but signal integrity or stability fails.
- `UNAVAILABLE`: board or codec cannot be discovered.

A rejected unit may be replaced without changing the studio core. The replacement adapter must publish the same logical capability contract or clearly report differences for routing policy.
