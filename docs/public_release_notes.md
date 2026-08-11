# Initial Public Release Notes

Velvet Audio Studio is being prepared for public release as an experimental local-first multichannel audio organ for the Velvet ecosystem.

## What is public-ready

The repository currently contains tested software foundations for:

- hardware-neutral Studio booking and channel leases;
- Audio Injector Octo capture/playback adapters;
- deterministic local voice activity detection;
- bounded utterance capture;
- offline Vosk transcription and wake-name privacy gating;
- local Piper TTS and bounded delivery profiles;
- Studio-owned multichannel playback with higher-priority speech preemption;
- Event Protocol speech-expression intake;
- durable Runtime event publishing and ingress acknowledgement;
- privacy-bounded output lifecycle evidence;
- compatibility with Velvet's canonical receipt path;
- service configuration, simulation, deployment, and hardware-acceptance documentation.

## What remains experimental

The target Raspberry Pi 3 + Audio Injector Octo deployment has not yet completed physical acceptance on the final pinned OS/kernel image.

That means public release should not be read as a claim that the final vehicle audio hardware is production-ready. Physical Vosk/Piper performance, exact Octo signal integrity, simultaneous capture/playback, thermals, routing, and real speaker preemption still require recorded acceptance evidence.

Playback therefore remains disabled by default.

## Why publish now

The software boundaries are useful independently of final hardware acceptance. Publishing now allows the architecture, adapters, tests, failure posture, privacy boundaries, and hardware-acceptance method to be inspected while the physical node is still being proven.

The project will continue to mark the distinction between **implemented**, **simulated/CI-tested**, and **physically accepted** capability explicitly.
