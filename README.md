# Velvet Audio Studio

Velvet Audio Studio is the local-first multichannel audio organ for the Velvet ecosystem.

It coordinates microphone capture, bounded local speech recognition, local speech synthesis, speaker routing, channel leases, priority handling, output evidence, and the hardware adapters that connect those capabilities to a Raspberry Pi and Audio Injector Octo.

The design goal is simple: **features do not seize audio hardware directly**. They request Studio capabilities. Studio owns the shared device boundary, reports what is actually available, and produces evidence about what happened.

## Current state

Implemented and covered by CI:

- hardware-neutral Studio requests, channel leases, and routing contracts
- Audio Injector Octo discovery and six-channel capture adapter
- deterministic local voice activity detection and bounded utterance capture
- offline Vosk transcription with a bounded worker and wake-name privacy gate
- local Piper TTS with bounded acoustic delivery profiles
- one Studio-owned persistent eight-channel ALSA playback stream
- preferred output-slot routing and strictly higher-priority speech preemption
- `language.expression.speech_requested` Event Protocol consumer
- durable Runtime event publishing with retry journal and ingress acknowledgements
- privacy-bounded audio-output lifecycle evidence
- canonical receipt compatibility for booking, start, completion, preemption, failure, and recovery
- reference Runtime ingress receiver, dispatch queue, Court-routing seam, and service packaging

Not yet physically accepted:

- Raspberry Pi 3 + Audio Injector Octo on the final pinned deployment image
- physical Vosk performance and microphone acceptance
- physical Piper performance, thermals, and voice-model acceptance
- simultaneous Octo capture/playback under vehicle-like load
- final per-speaker routing and occupied-slot safety-preemption checks through the real amps/speakers

The software path is intentionally ahead of the hardware acceptance state. A green x86 CI run proves the contracts and package surfaces; it does **not** prove the Raspberry Pi/Octo signal path.

See [`docs/hardware_acceptance.md`](docs/hardware_acceptance.md) and [`docs/known_issues.md`](docs/known_issues.md) before enabling physical playback.

## Architecture

```text
microphones
  -> Audio Studio capture
  -> bounded utterance
  -> local Vosk
  -> wake/privacy gate
  -> Runtime / Event Protocol

verified meaning
  -> Velvet Language
  -> language.expression.speech_requested
  -> Audio Studio validation
  -> bounded delivery profile
  -> local Piper
  -> mono PCM
  -> Studio lease
  -> resample + multichannel slot routing
  -> persistent ALSA stream
  -> Audio Injector Octo
  -> amps / speakers
  -> output evidence
  -> Runtime acknowledgement
  -> Velvet Receipts
```

Velvet Language owns the wording. Audio Studio owns acoustic rendering and local routing. Event Protocol transports the request and evidence. Receipts records evidence. None of those layers gain command or actuation authority merely because speech exists.

## Core rules

1. No handmaiden, feature, TTS engine, or caller opens the shared ALSA device directly.
2. Active routes are represented by Studio leases.
3. Hardware capability is discovered and reported truthfully rather than assumed.
4. Playback remains disabled until the physical output path passes acceptance.
5. Explicitly preemptive output requests may displace only strictly lower-priority conflicting leases.
6. Equal or higher-priority leases remain protected.
7. Raw utterance audio and unmatched transcript text stay local to the audio node.
8. Output evidence does not duplicate spoken text or PCM.
9. Evidence transport trouble does not become an accidental permission gate that silences safety speech.
10. Hardware can be replaced behind the adapter boundary without rewriting Studio contracts.

## Initial hardware target

- Raspberry Pi 3
- Audio Injector Octo
- Ethernet connection to Velvet Runtime
- Python 3.11 or newer

The Octo is the first multichannel reference unit, not a permanent architectural dependency. A future Velvet-native or third-party interface can replace it by implementing the same logical capability boundary.

Upstream hardware/software references and model-license responsibilities are documented in [`docs/upstream_and_provenance.md`](docs/upstream_and_provenance.md).

## Install for development

```bash
python -m pip install -e '.[dev]'
pytest
```

Optional offline speech recognition:

```bash
python -m pip install -e '.[speech]'
```

Optional local TTS:

```bash
python -m pip install -e '.[tts]'
```

Audio Studio does not download Vosk or Piper voice models during normal service startup. Deployment is responsible for provisioning, checking, and protecting local model files.

## Safe first run

Validate configuration without touching ALSA hardware or loading speech models:

```bash
velvet-audio validate-config --config config/studio.example.yaml
```

Inspect the assembly plan:

```bash
velvet-audio run --config config/studio.example.yaml --plan
```

Run one bounded simulated iteration and emit Event Protocol JSONL:

```bash
velvet-audio run \
  --config config/studio.example.yaml \
  --source simulated \
  --runtime-mode stdout \
  --max-iterations 1
```

The example configuration keeps physical playback disabled.

## Offline transcription

Vosk is loaded lazily from a local model path. Completed utterances enter a bounded worker so decoding cannot block continuous capture.

The wake-name gate recognizes configured local wake phrases such as `hey velvet`, `velvet`, and `princess`. Unmatched text remains on the audio node. A wake match releases only the request text after the wake phrase, and the resulting event carries no command authority.

See [`docs/offline_transcription.md`](docs/offline_transcription.md).

## Local TTS and delivery profiles

Piper renders already-approved text into local PCM. Audio Studio exposes bounded named profiles instead of arbitrary caller-controlled synthesis knobs:

- `owner_default`
- `guest_reserved`
- `high_driving_load`
- `warning`
- `emergency`
- `quiet_night`
- `playful_social`

Emergency, warning/critical, and high-driving-load context override lower-consequence requested styles. Piper does not choose wording, authority, speaker hardware, or output channels.

See [`docs/offline_tts.md`](docs/offline_tts.md).

## Output evidence and receipts

The output path emits the authority-free `velvet.audio-output-evidence.v1` lifecycle:

```text
audio.output.booked
  -> audio.output.started
  -> audio.output.completed
```

A displaced clip ends with `audio.output.preempted`. Synthesis, booking, or playback failures emit `audio.output.failed`; the first later proven clean completion can emit `audio.output.recovered`.

Evidence may contain request/expression IDs, priority, logical output slots, model/profile identifiers, rates, frame counts, durations, and preemption/recovery relationships. It intentionally excludes spoken text, transcripts, raw PCM, ALSA paths, local model paths, capability tokens, and actuation claims.

The existing durable Runtime journal carries this evidence. Velvet Receipts can then normalize accepted evidence into its append-only canonical receipt chain. A Runtime ingress acknowledgement and a canonical Velvet receipt are deliberately different pieces of evidence.

## Physical deployment warning

The Audio Injector Octo depends on Raspberry Pi kernel, ASoC, I2S/clocking, and device-tree behavior. Card enumeration alone is not acceptance. A deployment must prove the actual input/output signal path on the exact pinned OS and kernel image.

Physical acceptance includes per-channel playback/capture, sustained streaming, reboot discovery, concurrent capture/playback, Piper/Vosk load, thermals, routing, preemption, underrun/overrun behavior, and evidence delivery.

See:

- [`docs/hardware_acceptance.md`](docs/hardware_acceptance.md)
- [`docs/known_issues.md`](docs/known_issues.md)
- [`docs/offline_tts.md`](docs/offline_tts.md)
- [`docs/offline_transcription.md`](docs/offline_transcription.md)

## Runtime integration

A reference Runtime receiver is included for vehicle-LAN integration tests and durable-ingress development. It validates envelopes and idempotency, persists canonical event bytes, returns durable ingress acknowledgements, and supports ordered downstream dispatch.

The reference dispatch path places a Court decision before routing. Repetition alone does not authorize skipping work, and only explicitly classified permanent failures can become bounded quarantine candidates.

Deployment details live in:

- [`docs/runtime_http_contract.md`](docs/runtime_http_contract.md)
- [`docs/runtime_receiver_deployment.md`](docs/runtime_receiver_deployment.md)
- [`docs/runtime_ingress_dispatch.md`](docs/runtime_ingress_dispatch.md)
- [`docs/runtime_dispatch_worker.md`](docs/runtime_dispatch_worker.md)

## Service packaging

The hardened audio service unit is in `packaging/systemd/velvet-audio.service`. It uses a dedicated service account, explicit ALSA access, managed durable state, configuration validation, and bounded restart behavior.

Installation notes are in [`packaging/systemd/README.md`](packaging/systemd/README.md).

## Contributing

Start with [`CONTRIBUTING.md`](CONTRIBUTING.md). New capabilities must first check for an existing owner, contract, adapter, or implementation before adding another path. Hardware claims require evidence, and direct device access that bypasses Studio ownership is not accepted.

Security guidance is in [`SECURITY.md`](SECURITY.md).

## License

Velvet Audio Studio is released under GPL-3.0. Third-party packages, hardware reference files, and speech/voice models retain their own licenses. See [`docs/upstream_and_provenance.md`](docs/upstream_and_provenance.md) before redistributing external models or hardware design material.
