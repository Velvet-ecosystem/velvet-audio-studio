# Offline Piper TTS and Delivery Profiles

Velvet Audio Studio synthesizes speech locally. Text-to-speech is an expression and audio-rendering capability, not a reasoning, memory, identity, authority, or actuation capability.

## Ownership boundary

The intended path is:

```text
verified meaning
  -> Velvet Language chooses truthful wording
  -> speech request carries bounded delivery context
  -> Audio Studio resolves an acoustic delivery profile
  -> Piper renders local PCM
  -> Audio Studio routing/playback owns the speaker path
```

Language owns what is said. Audio Studio owns how approved text is rendered into audio and where that audio is routed. Piper does not decide facts, intent, permissions, persona policy, or actions.

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
```

Configuration fails closed when TTS is enabled and either local voice file is missing. Unknown configuration keys and unknown delivery profiles are rejected.

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

## Resource and privacy bounds

- synthesis requests are bounded to 4096 normalized text characters;
- voice models are loaded lazily and remain local;
- synthesized PCM remains inside the audio path unless an explicit diagnostic process saves it;
- TTS metadata should prefer model ID, profile ID, duration, format, and text length rather than duplicating full spoken text into unrelated receipts;
- synthesis failure must not grant authority or bypass deterministic safety fallback language.

## Raspberry Pi acceptance

An x86 CI import proves package/API compatibility only. Before enabling Piper on the physical audio node, record:

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
- Octo playback stability while Piper is active;
- clean stop/reload behavior;
- each delivery profile through the physical speaker path.

Do not assume a 32-bit ARM wheel is available. The deployment image must prove that its architecture, Python version, Piper package, and the known-good Audio Injector Octo kernel can coexist before TTS is marked accepted.

## Current boundary

The repository now contains a lazy local Piper synthesizer, strict local voice configuration, bounded delivery profiles, safety-first profile resolution, PCM output contracts, service assembly, and tests. Final production work still has to connect synthesized PCM into the receipted Audio Studio playback/mixing path and connect Language's rendered expressions to the speech-request boundary.
