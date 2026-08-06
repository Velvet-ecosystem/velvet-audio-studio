# Offline Transcription and Wake-Name Gate

Velvet Audio Studio performs speech recognition locally. The transcription layer does not download models, call a cloud API, interpret commands, or grant authority.

## Processing path

1. Six-channel capture is analyzed and lifecycle-gated.
2. The selected healthy microphone becomes a bounded local utterance.
3. The utterance is submitted to a bounded transcription worker.
4. Vosk resamples the mono utterance to the configured recognizer rate and consumes signed little-endian PCM16.
5. The full transcript remains local to the audio node.
6. The wake-name gate checks only the beginning of the normalized transcript.
7. Unmatched speech produces metadata without transcript text.
8. Matched speech releases only the request text after the wake name.
9. The released event carries `command_authority: false` and must still pass Runtime Court and later interpretation boundaries.

The transcription worker runs outside the continuous capture loop. A slow model therefore cannot directly block ALSA reads. Queue saturation, model startup failure, decode failure, and shutdown timeout are explicit Runtime events.

## Install the optional engine

Install the studio with the optional speech dependency on the audio node:

```bash
python -m pip install -e '.[speech]'
```

The default package and CI test environment do not require Vosk. Import is lazy and occurs only when transcription is enabled and the worker starts.

## Provision a model

Models are provisioned out of band and stored locally. The initial Raspberry Pi target is:

```text
vosk-model-small-en-us-0.15
```

A suggested root-owned installation directory is:

```text
/usr/share/velvet-audio/models/vosk-model-small-en-us-0.15
```

The directory should be readable by the `velvet-audio` service but not writable by it. The service never downloads or replaces a model. Model acquisition, checksum verification, license review, extraction, ownership, and promotion are deployment responsibilities.

## Configuration

```yaml
transcription:
  enabled: true
  engine: vosk
  model_path: /usr/share/velvet-audio/models/vosk-model-small-en-us-0.15
  recognizer_sample_rate_hz: 16000
  language: en-us
  include_words: true
  max_alternatives: 0
  log_level: -1
  grammar: []
  queue_capacity: 4
  worker_stop_timeout_seconds: 10.0
  wake_names:
    - hey velvet
    - velvet
    - princess
```

`validate-config` checks the section, rejects unknown keys, and requires an existing local model directory when enabled. Transcription also requires `voice_frontend.enabled: true`.

An empty grammar uses the model vocabulary. A configured grammar is passed directly to the Vosk recognizer and should be used only when the intended vocabulary is genuinely bounded.

## Event privacy boundary

`audio.transcription.completed` contains model identity, language, confidence, word count, duration, and text length. It does not contain transcript text or samples.

`audio.wake_name.not_matched` contains no transcript text.

`audio.wake_name.matched` contains the normalized wake name and request text after that name. It does not contain the full transcript or samples and explicitly reports `command_authority: false`.

`audio.transcription.queue_full`, `audio.transcription.unavailable`, and `audio.transcription.failed` preserve failure evidence without embedding audio.

## Pi acceptance

Before enabling transcription in the vehicle, record:

- Raspberry Pi model and revision
- OS image and kernel
- Python and pip versions
- Vosk package version and native library path
- model identifier, checksum, size, and license
- cold model-load time
- resident memory before and after model load
- real-time factor for short, medium, and maximum-duration utterances
- CPU temperature and throttling state during repeated recognition
- queue depth under back-to-back utterances
- transcription accuracy in quiet cabin, road noise, music, fan noise, and multiple-speaker conditions
- false wake rate and missed wake rate for every configured name
- clean worker stop and restart behavior

A successful import on x86 CI is not Raspberry Pi acceptance. The exact Pi, wheel, model, OS, and kernel combination must be tested and receipted.

## Authority boundary

A wake-name match proves only that local speech recognition produced an addressed text observation. It does not prove speaker identity, owner presence, intent, safety, permission, or physical authority. Those decisions remain downstream responsibilities of presence gates, interpretation, Runtime Court, capabilities, safety gates, and receipts.
