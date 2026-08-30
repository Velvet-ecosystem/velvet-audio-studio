# Runtime speech-expression ingress

Audio Studio accepts approved `language.expression.speech_requested` events from Velvet Runtime without giving Runtime ownership of synthesis, speaker routing, playback priority, or audio hardware.

## Single-owner rule

The Audio Injector Octo playback endpoint has one Studio owner.

Do not run a separate speech-output process beside the normal Audio Studio service. The speech-enabled deployment uses `velvet_audio_studio.integrated_speech_service`, which builds the normal capture/transcription/playback assembly once and runs only the Runtime speech HTTP accept/dispatch loop in a background thread.

This preserves one:

- ALSA playback sink
- `StudioSessionManager`
- Piper synthesizer lifecycle
- Audio output evidence emitter
- capture/transcription service process

## Transport boundary

Runtime sends the existing shared speech-expression event inside Audio Studio's strict transport envelope:

```text
EventProtocolEnvelope
  event_type: language.expression.speech_requested
  payload:
    speech_expression: <complete validated shared speech event>
```

The outer envelope owns transport sequence and idempotency. The nested event remains the shared Language/Event Protocol contract and is not reinterpreted in transit.

Audio validates the nested speech event twice:

1. before durable HTTP acknowledgement
2. again immediately before acoustic dispatch

Invalid, malformed, authority-bearing, or hardware-selecting speech is rejected before it enters the durable dispatch queue.

## Three distinct truths

The speech path deliberately preserves three separate facts:

1. **Accepted**: the HTTP receiver durably stored a valid transport event and returned its acknowledgement receipt.
2. **Claimed/processed**: the leased local dispatcher handed the event to the speech handler and recorded its downstream delivery receipt.
3. **Acoustic attempt**: the private speech-delivery ledger records `started`, `completed`, or `uncertain` for the expression.

An HTTP `202` does not mean Velvet spoke.

Audio output lifecycle evidence remains a separate existing path for booked, started, completed, preempted, failed, and recovered output events.

## Duplicate and crash behavior

The acoustic-attempt ledger is keyed by the stable Language expression identity and a SHA-256 digest of the complete nested speech event. It does not store the spoken sentence.

- a completed expression is not spoken again if Runtime retries delivery
- reusing one expression identity with changed content is rejected
- if playback raises after an attempt has started, the expression becomes `uncertain`
- if the process disappears while state is still `started`, recovery changes it to `uncertain`
- `uncertain` speech is not automatically replayed

This is intentional. After an ambiguous crash Audio cannot safely prove whether some or all of a warning was already audible, so repeating it automatically would invent certainty.

## Network exposure

The integrated service binds `127.0.0.1` by default. Any non-loopback bind requires a bearer-token file.

For the packaged LAN-facing service create a protected token:

```bash
sudo install -o root -g velvet-audio -m 0640 /dev/null \
  /etc/velvet-audio/speech-ingress.token
sudoedit /etc/velvet-audio/speech-ingress.token
```

The packaged unit binds port `8766` and `/v1/speech-expressions` and stores acknowledgement, dispatch, and acoustic-attempt state in:

```text
/var/lib/velvet-audio/speech-ingress.sqlite3
```

The database contains the nested event while reliable local dispatch requires it. The separate `speech_expression_delivery` table stores identity, hashes, state, timestamps, and failure class only, not spoken text.

## systemd deployment

`packaging/systemd/velvet-audio-with-speech.service` is an alternative to `velvet-audio.service`, not a companion unit. The units conflict intentionally because both would otherwise attempt to own the same capture/playback hardware.

Install the speech-enabled unit:

```bash
sudo install -o root -g root -m 0644 \
  packaging/systemd/velvet-audio-with-speech.service \
  /etc/systemd/system/velvet-audio-with-speech.service
sudo systemctl daemon-reload
sudo systemctl disable --now velvet-audio.service
sudo systemctl enable --now velvet-audio-with-speech.service
```

Playback, Piper, Vosk, and concurrent Octo capture/playback remain subject to the physical Raspberry Pi hardware-acceptance checklist. Software CI does not substitute for that proof.

## Authority boundary

Speech ingress does not grant:

- command authority
- actuation authority
- speaker identity or owner-presence proof
- Runtime-selected speaker channels
- Runtime-selected ALSA devices
- Runtime-selected TTS models
- Runtime-selected synthesis controls
- bypass of Audio Studio priority or delivery profiles

Language owns approved wording. Runtime transports it. Audio Studio owns the acoustic edge.
