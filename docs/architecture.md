# Audio Studio Architecture

## Purpose

Velvet Audio Studio is the shared multichannel sound organ for Velvet. Its first hardware target is a Raspberry Pi 3 with an Audio Injector Octo, connected to Velvet Runtime over Ethernet.

The studio is not owned by one handmaiden. Lyra, Echo, Temperance, navigation, calls, media, system alerts, and Velvet's primary voice all request studio sessions through the same booking boundary.

## Rules

1. No caller opens ALSA devices directly.
2. Every active route is represented by a channel lease.
3. Safety audio outranks every other session.
4. Higher-priority speech may preempt lower-priority speech at a bounded playback-period boundary.
5. Hardware loss degrades audio without taking down Velvet Runtime.
6. Hardware adapters report health honestly and never claim unavailable channels.
7. Simulation and physical hardware use the same request and lease contracts.
8. Route, gain, preemption, failure, and recovery actions must be receipted before production acceptance.
9. Playback is disabled until the physical output path passes capability and signal acceptance.

## Current layers

- `contracts.py`: hardware-neutral requests, priorities, preferred output slots, and leases.
- `channel_registry.py`: channel inventory and allocation.
- `session_manager.py`: booking lifecycle.
- `pcm.py`: shared PCM normalization, resampling, and channel routing helpers.
- `playback_engine.py`: serialized Studio speech playback and bounded priority preemption.
- `voice/output_service.py`: approved-text synthesis, lease acquisition, playback, and guaranteed release.
- `adapters/alsa/`: shared ALSA capability and PCM-format boundaries.
- `adapters/audio_injector_octo/alsa_capture.py`: persistent six-channel capture process.
- `adapters/audio_injector_octo/alsa_playback.py`: persistent eight-channel playback process.
- `adapters/audio_injector_octo/capture_factory.py`: identity and capability acceptance for capture.
- `adapters/audio_injector_octo/playback_factory.py`: identity and capability acceptance for playback.
- `adapters/audio_injector_octo/channel_map.py`: logical cabin names to physical Octo slots.
- `config/`: deployment-specific node, capture, playback, speech, and network configuration.

## Speech output path

```text
approved wording
  -> bounded delivery context
  -> Piper local synthesis
  -> mono S16_LE PCM
  -> Studio channel lease
  -> sample-rate conversion
  -> selected Octo output slots
  -> interleaved accepted playback format
  -> one persistent Studio-owned aplay stream
  -> Audio Injector Octo
  -> amps / speakers
```

Piper never owns the sound device. The output service synthesizes before booking so model latency does not unnecessarily hold a speaker channel. Once synthesis is ready, Studio books the requested slots and the playback engine writes period-sized frames. A higher-priority request can cancel a lower-priority clip at the next period boundary.

The current engine is intentionally serialized. It is the first safe speaker bridge, not the final concurrent mixer. Concurrent music, calls, navigation, alerts, and speech mixing/ducking remain a later layer behind the same lease and single-owner ALSA boundary.

## Planned links

- Velvet Event Protocol for speech requests, health, route changes, playback completion, preemption, and degradation events.
- Velvet Receipts for bookings, preemption, gain changes, playback completion, and failures.
- Velvet Runtime for node registration, capability advertisement, and lifecycle.
- Simulated Body Layer for fake channels, dropout, delay, stale state, impossible-device tests, and sink failures.
- Velvet Language for the neutral `RenderedExpression` to speech-request bridge.

## Hardware target

The Octo is treated as a six-input, eight-output device only after physical probing confirms the active ALSA topology. Capture and playback formats, rates, period sizes, card identity, and channel behavior remain deployment evidence rather than assumptions. Configuration is overrideable because codec overlays and exposed PCM behavior may differ by pinned installation image.
