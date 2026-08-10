# Offline Piper TTS and Delivery Profiles

Velvet Audio Studio synthesizes speech locally. Text-to-speech is an expression and audio-rendering capability, not a reasoning, memory, identity, authority, or actuation capability.

## Ownership boundary

The intended path is:

```text
verified meaning
  -> Velvet Language chooses truthful wording
  -> language.expression.speech_requested
  -> Runtime/Event Protocol routing
  -> Audio Studio validates the expression event
  -> Audio Studio resolves an acoustic delivery profile
  -> Piper renders local PCM
  -> Studio obtains a channel lease
  -> Studio resamples and maps PCM into leased Octo slots
  -> one Studio-owned ALSA playback stream reaches the speakers
  -> Audio Studio emits privacy-bounded output evidence
  -> Runtime durable ingress acknowledgement
  -> Velvet Receipts canonical evidence chain
```

Language owns what is said. Audio Studio owns how approved text is rendered into audio and where that audio is routed. Piper does not decide facts, intent, permissions, persona policy, or actions. Event Protocol transports output evidence, and Velvet Receipts owns canonical evidence retention; neither creates authority.

## Speech expression event boundary

`SpeechExpressionEventHandler` consumes the Event Protocol contract `velvet.speech-expression.v1` and event type `language.expression.speech_requested`. It validates the event again on the audio side before converting it to `SpeechOutputRequest` and entering `LocalSpeechOutputService`.

The event may carry approved wording, severity, audience, driving load, emergency context, and bounded presentation hints. It must declare `command_authority: false`, `actuation_authority: false`, `hardware_selected: false`, and `synthesis_selected: false`.

Audio Studio rejects events that try to smuggle in ALSA devices, output channel numbers, speaker IDs, voice-model paths, gain/volume/pitch/rate controls, Piper implementation knobs, capability tokens, executors, or authorization. Physical output slots therefore remain local Audio Studio configuration even when Language requests a named delivery posture.

Emergency context is independently promoted to Audio Studio safety severity. A lower-consequence requested style cannot weaken the existing safety-first delivery profile selector.

## Local engine

Install the optional TTS dependency on nodes that synthesize speech:

```bash
python -m pip install -e '.[tts]'
```

The TTS extra uses `piper-tts>=1.6,<2` and the current Piper Python API (`PiperVoice` plus `SynthesisConfig`). The service never downloads a voice model. Voice acquisition, license review, checksum verification, storage, and promotion are deployment responsibilities.

A voice consists of an ONNX model and its JSON configuration, for example:

```text
/usr/share/velvet-audio/voices/velvet.onnx
/usr/share/velvet-audio/voices/velvet.onnx.json
```

Both files should be root-owned, readable by the audio service, and not writable by it.

## Configuration

```yaml
tts:
  enabled: true
  engine: piper
  model_path: /usr/share/velvet-audio/voices/velvet.onnx
  config_path: /usr/share/velvet-audio/voices/velvet.onnx.json
  use_cuda: false
  default_profile: owner_default

playback:
  enabled: true
  source: alsa_octo
  identity_terms:
    - audioinjector
    - octo
  pcm_device: 0
  use_plughw: false
  sample_rate_hz: 48000
  sample_format: S32_LE
  period_frames: 480
  default_output_channels:
    - 4
```

Configuration fails closed when TTS is enabled and either local voice file is missing. Unknown TTS keys and unknown delivery profiles are rejected. Playback remains disabled by default until the physical Octo is accepted. When enabled, playback validates the configured output slots and the service assembly probes the actual ALSA playback endpoint before creating the sink.

## Bounded delivery profiles

Audio Studio exposes named profiles rather than arbitrary caller-controlled synthesis values:

- `owner_default` - normal owner conversation
- `guest_reserved` - more restrained guest delivery
- `high_driving_load` - shorter, steadier high-load delivery
- `warning` - clear warning delivery
- `emergency` - fastest and least variable safety delivery
- `quiet_night` - reduced output level for quiet environments
- `playful_social` - greater permitted variation for low-consequence social speech

These profiles tune Piper's supported synthesis controls: phoneme length scale, volume, generator noise, phoneme-width noise, and audio normalization. They do not rewrite text or invent emotion labels.

Safety context outranks requested style. An emergency request resolves to `emergency` even if a caller requests `playful_social`. Warning/critical context and high driving load similarly override lower-consequence style requests. `playful_social` requires explicit social permission.

## Speaker bridge

`LocalSpeechOutputService` is the composition boundary between approved speech and the Audio Studio output path. It synthesizes first so a slow TTS operation does not hold a speaker lease, then books the requested Studio output slots immediately before playback. The lease is always released, including when playback fails.

Speech bookings opt into bounded output preemption. If the requested preferred slots are occupied only by strictly lower-priority output leases, `StudioSessionManager` releases those lower leases and grants the higher-priority request. Equal or higher-priority leases cannot be displaced, input-channel bookings are never displaced by this path, and ordinary Studio requests remain non-preemptive unless they explicitly opt in. `StudioBookingResult` preserves the displaced lease identities so the evidence layer can report what was actually displaced.

`StudioSpeechPlaybackEngine` accepts Piper's mono S16_LE PCM, resamples it to the accepted playback rate, duplicates it only into the leased output slots, and writes interleaved multichannel periods to the sink. A normal Velvet voice can therefore target the configured center-voice slot while warning or emergency speech may request a different verified route. When higher-priority speech preempts a lower clip, the lower playback result preserves the incoming request ID that caused the preemption.

`AlsaOctoPlaybackSink` owns one persistent raw `aplay` process configured for the accepted eight-channel Octo PCM. Piper, Language, handmaidens, and individual features never open ALSA directly.

The first bridge is deliberately serialized. It provides one-owner hardware access and bounded speech priority preemption without pretending a concurrent media mixer already exists. If higher-priority speech arrives while lower-priority speech is active, the booking layer can take the lower output lease and the playback layer cancels the lower clip at the next playback-period boundary. The later mixer can add simultaneous sources and ducking behind the same Studio lease and sink contracts.

## Output evidence and canonical receipts

Audio Studio now emits the authority-free Event Protocol family `velvet.audio-output-evidence.v1` for:

- `audio.output.booked`
- `audio.output.started`
- `audio.output.completed`
- `audio.output.preempted`
- `audio.output.failed`
- `audio.output.recovered`

The emitter uses the same `ReliablePublishedCapturePipeline.publish_events()` path as capture and voice-front-end service events. Output evidence therefore shares the existing durable ordered journal, backlog supervision, retry behavior, and Runtime acknowledgement path. There is no second audio evidence queue or private logger.

Evidence includes operational identifiers and measurements such as request/expression IDs, priority, logical output slots, profile/model IDs, sample rates, frame counts, duration, displaced/preempting request IDs, and bounded failure/recovery classification. It deliberately excludes the spoken text, transcript, raw PCM, ALSA paths, voice-model filesystem paths, capability tokens, and authority fields.

Failure events do not serialize raw exception messages because synthesis engines can echo input text into exceptions. Canonical evidence records only the failure stage and stable exception class. Detailed diagnostics belong in a protected local diagnostic log.

Evidence publishing is not an audio-authority gate. A Runtime transport outage must not silence safety speech. Events are submitted to the existing durable Runtime pipeline; an unexpected internal publishing exception is retained as local emitter health state rather than being converted into permission to block the audio operation.

`velvet-receipts` normalizes accepted output events into `velvet.receipts.audio-output.v1` and the existing append-only hash chain. A Runtime ingress acknowledgement and a canonical Velvet receipt are intentionally different evidence:

- the Runtime acknowledgement proves durable ingress acceptance of the event;
- the canonical Velvet receipt proves that accepted evidence was normalized into the append-only receipt chain.

Neither grants permission to speak, execute, actuate, or own a channel.

## Resource and privacy bounds

- synthesis requests are bounded to 4096 normalized text characters;
- voice models are loaded lazily and remain local;
- synthesized PCM remains inside the audio path unless an explicit diagnostic process saves it;
- canonical output evidence does not duplicate spoken text or raw audio;
- failure receipts do not copy exception messages that could contain speech text;
- synthesis failure must not grant authority or bypass deterministic safety fallback language;
- playback only uses channels present in a valid Studio lease;
- callers never receive a direct ALSA file/device handle;
- evidence publication failure does not become an implicit command/speech denial.

## Raspberry Pi acceptance

An x86 CI import proves package/API compatibility only. Before enabling Piper and playback on the physical audio node, record:

- Raspberry Pi model and revision;
- 32-bit versus 64-bit userspace;
- OS image and kernel;
- Python and `piper-tts` versions;
- wheel or source-build provenance;
- voice model/config identifiers, checksums, sizes, and licenses;
- cold model-load time;
- resident memory before and after model load;
- synthesis real-time factor for short, normal, and long responses;
- CPU temperature and throttling during repeated synthesis;
- concurrent capture plus synthesis behavior;
- actual Octo playback PCM name, sample format, sample rate, period size, and eight-channel capability;
- center-voice and alternate-slot routing checks;
- priority preemption behavior through the physical speaker path;
- output evidence ordering and Runtime retry during playback;
- Octo playback stability while Piper is active;
- clean sink and synthesizer stop/reload behavior;
- each delivery profile through the physical speaker path.

Do not assume a 32-bit ARM wheel is available. The deployment image must prove that its architecture, Python version, Piper package, and the known-good Audio Injector Octo kernel can coexist before TTS is marked accepted.

## Current boundary

The repository now contains a lazy local Piper synthesizer, strict local voice configuration, bounded delivery profiles, safety-first profile resolution, the speech-expression Event Protocol consumer, shared PCM conversion, an identity-probed eight-channel Octo playback sink, preferred and explicitly preemptive Studio output leases, a period-bounded priority-aware speech playback engine, privacy-bounded output lifecycle evidence routed through the existing durable Runtime pipeline, configured service assembly, and tests. `velvet-receipts` owns canonical normalization of that output evidence. Remaining production boundaries are deployed Runtime subscription/routing for incoming speech-expression events, the later concurrent multi-source mixer/ducking layer, and physical Pi/Octo acceptance.
