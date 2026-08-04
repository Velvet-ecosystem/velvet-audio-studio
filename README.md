# Velvet Audio Studio

Velvet’s shared multichannel audio organ for Raspberry Pi and Audio Injector Octo hardware.

This repository owns studio booking, channel leases, routing, mixing policy, priority ducking, microphone capture, voice playback, alerts, music sessions, device health, and the hardware adapters that connect Velvet to the Pi and Octo.

## Initial hardware target

- Raspberry Pi 3
- Audio Injector Octo
- Ethernet connection to Velvet Runtime

## Core rule

No handmaiden or feature seizes ALSA hardware directly. Lyra, Echo, Temperance, navigation, calls, and Velvet’s main voice request routes through the studio. Safety-critical audio preempts or ducks lower-priority sessions, and every route remains observable and recoverable.

## Status

Foundation scaffold in progress.
