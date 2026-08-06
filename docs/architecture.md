# Audio Studio Architecture

## Purpose

Velvet Audio Studio is the shared multichannel sound organ for Velvet. Its first hardware target is a Raspberry Pi 3 with an Audio Injector Octo, connected to Velvet Runtime over Ethernet.

The studio is not owned by one handmaiden. Lyra, Echo, Temperance, navigation, calls, media, system alerts, and Velvet's primary voice all request studio sessions through the same booking boundary.

## Rules

1. No caller opens ALSA devices directly.
2. Every active route is represented by a channel lease.
3. Safety audio outranks every other session.
4. Higher-priority sessions may duck or preempt lower-priority sessions.
5. Hardware loss degrades audio without taking down Velvet Runtime.
6. Hardware adapters report health honestly and never claim unavailable channels.
7. Simulation and physical hardware use the same request and lease contracts.
8. Route, gain, preemption, failure, and recovery actions must be receipted.

## Initial layers

- `contracts.py`: hardware-neutral requests, priorities, and leases.
- `channel_registry.py`: channel inventory and allocation.
- `session_manager.py`: booking lifecycle and future preemption policy.
- `adapters/raspberry_pi.py`: Raspberry Pi host discovery.
- `adapters/audio_injector_octo.py`: Octo device, routing, and mixer boundary.
- `config/`: deployment-specific channel and node configuration.

## Planned links

- Velvet Event Protocol for requests, health, route changes, and degradation events.
- Velvet Receipts for bookings, preemption, gain changes, and failures.
- Velvet Runtime for node registration, capability advertisement, and lifecycle.
- Simulated Body Layer for fake channels, dropout, delay, stale state, and impossible-device tests.

## Hardware target

The Octo is treated as a six-input, eight-output device until physical probing confirms the active ALSA topology. Configuration must remain overrideable because codec overlays and exposed channel names may differ by installation image.
