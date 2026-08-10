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
- accepted playback period size
- mixer controls
- codec and clock status when observable
- per-channel playback result
- per-channel capture result
- loopback or known-source result
- concurrent capture/playback result
- underrun, overrun, distortion, and clock-slip observations
- final health state and degraded reason

## Required tests

1. Discover the card by identity rather than numeric card order.
2. Verify one six-channel capture endpoint and one eight-channel playback endpoint, or record the actual exposed capability.
3. Play an identifiable signal through every physical output.
4. Capture a known signal through every physical input.
5. Run concurrent capture and playback.
6. Run a sustained stream long enough to expose clock or buffer instability.
7. Verify the Studio-owned persistent `aplay` stream survives multiple sequential speech clips without reopening the device.
8. Verify a center-voice lease reaches only the configured center-voice physical output.
9. Verify an alternate two-slot route reaches only those two physical outputs.
10. Occupy the center-voice slot with lower-priority speech, then verify a safety-priority speech request explicitly takes that lease and the lower clip stops at the next playback-period boundary.
11. Verify equal-priority or higher-priority occupancy is not displaced by the preemption path.
12. Reboot and repeat discovery to prove stable startup.
13. Save a receipt before marking the unit available.

## Offline transcription evidence

When Vosk transcription is enabled, hardware acceptance also records:

- Python, pip, Vosk package, and native-library versions
- local model identifier, checksum, size, and license
- cold model-load time
- resident memory and CPU load while idle and decoding
- real-time factor for representative utterance lengths
- CPU temperature and throttling observations
- queue depth during back-to-back utterances
- recognition results in quiet, road noise, music, HVAC noise, and overlapping speech
- false and missed wake-name results for `hey velvet`, `velvet`, and `princess`
- worker restart and clean-shutdown results

## Offline TTS evidence

When Piper TTS is enabled, hardware acceptance also records:

- 32-bit versus 64-bit userspace
- Python, pip, Piper package, and ONNX Runtime versions
- wheel or source-build provenance
- local voice model/config identifiers, checksums, sizes, and licenses
- cold voice-load time
- resident memory before and after voice load
- synthesis real-time factor for short, normal, and long responses
- CPU temperature and throttling during repeated synthesis
- concurrent capture plus synthesis behavior
- synthesized PCM format and duration consistency
- source-to-playback resampling result on the accepted Octo rate
- physical playback of every bounded delivery profile
- preferred output-slot routing evidence
- occupied-slot safety-preemption result through the real amps/speakers
- distortion, underrun, clipping, and intelligibility under road/HVAC/music noise
- clean synthesizer and playback-sink close/reload behavior

An x86 CI import proves packaging only. It does not accept the Raspberry Pi deployment. Vosk, Piper, the ALSA playback sink, and concurrent Octo capture/playback must each pass on the exact pinned Pi image used with the Octo.

## Acceptance states

- `ACCEPTED`: all required tests pass.
- `DEGRADED`: usable with explicit missing channels or restrictions.
- `REJECTED`: enumeration succeeds but signal integrity or stability fails.
- `UNAVAILABLE`: board, codec, model, or native speech engine cannot be discovered.

A rejected unit may be replaced without changing the studio core. The replacement adapter must publish the same logical capability contract or clearly report differences for routing policy.
