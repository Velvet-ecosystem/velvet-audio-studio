# Velvet Audio Studio

Velvet’s shared multichannel audio organ for Raspberry Pi and Audio Injector Octo hardware.

This repository owns studio booking, channel leases, routing, mixing policy, priority ducking, microphone capture, voice playback, alerts, music sessions, device health, and the hardware adapters that connect Velvet to the Pi and Octo.

## Initial hardware target

- Raspberry Pi 3
- Audio Injector Octo
- Ethernet connection to Velvet Runtime

## Core rule

No handmaiden or feature seizes ALSA hardware directly. Lyra, Echo, Temperance, navigation, calls, and Velvet’s main voice request routes through the studio. Safety-critical audio preempts or ducks lower-priority sessions, and every route remains observable and recoverable.

## Hardware boundary

The studio core is hardware-neutral. Raspberry Pi host setup, ALSA discovery, and Audio Injector Octo details live behind adapters. The Octo is the initial unit, not a permanent cage: a future multichannel interface may replace it without rewriting booking, priority, routing, or receipt logic.

## Known compatibility warning

Recent Raspberry Pi kernels may expose the Octo while still producing unstable or distorted playback because the board depends on older ASoC, I2S, clocking, and device-tree behavior. Deployment must begin from a tested, pinned Raspberry Pi OS and kernel image. Do not assume the newest image is the safest image.

See `docs/known_issues.md` and `docs/hardware_acceptance.md` before connecting the physical board.

## Service configuration

`config/studio.example.yaml` defines the node identity, capture source, stable Octo identity terms, PCM format, sample rate, period size, heartbeat cadence, retry journal, and Runtime backlog policy.

Validate configuration without touching ALSA hardware:

```bash
velvet-audio validate-config --config config/studio.example.yaml
```

Resolve the configured source and print the assembly plan without opening it:

```bash
velvet-audio run --config config/studio.example.yaml --plan
```

Run a bounded simulated smoke test. Event Protocol envelopes are emitted as canonical JSON lines and the final service summary goes to standard error:

```bash
velvet-audio run \
  --config config/studio.example.yaml \
  --source simulated \
  --runtime-mode stdout \
  --max-iterations 1
```

Run the configured ALSA Octo source until interrupted:

```bash
velvet-audio run --config config/studio.example.yaml --runtime-mode stdout
```

`stdout` is the local development transport. `unavailable` intentionally fails every Runtime delivery so ordered events remain in the durable retry journal for outage and restart testing. The real Ethernet Event Protocol transport will replace this launch-time development choice without changing capture, queue, or service logic.

## Status

Foundation scaffold in progress on `foundation/initial-studio-tree`.
