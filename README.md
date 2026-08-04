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

## Status

Foundation scaffold in progress on `foundation/initial-studio-tree`.
