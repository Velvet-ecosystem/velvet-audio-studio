# Velvet Audio Studio

Velvet’s shared multichannel audio organ for Raspberry Pi and Audio Injector Octo hardware.

This repository owns studio booking, channel leases, routing, mixing policy, priority ducking, microphone capture, voice playback, alerts, music sessions, device health, and the hardware adapters that connect Velvet to the Pi and Octo.

## Initial hardware target

- Raspberry Pi 3
- Audio Injector Octo
- Ethernet connection to Velvet Runtime
- Python 3.11 or newer

## Core rule

No handmaiden or feature seizes ALSA hardware directly. Lyra, Echo, Temperance, navigation, calls, and Velvet’s main voice request routes through the studio. Safety-critical audio preempts or ducks lower-priority sessions, and every route remains observable and recoverable.

## Hardware boundary

The studio core is hardware-neutral. Raspberry Pi host setup, ALSA discovery, and Audio Injector Octo details live behind adapters. The Octo is the initial unit, not a permanent cage: a future multichannel interface may replace it without rewriting booking, priority, routing, or receipt logic.

## Known compatibility warning

Recent Raspberry Pi kernels may expose the Octo while still producing unstable or distorted playback because the board depends on older ASoC, I2S, clocking, and device-tree behavior. Deployment must begin from a tested, pinned Raspberry Pi OS and kernel image. Do not assume the newest image is the safest image.

See `docs/known_issues.md` and `docs/hardware_acceptance.md` before connecting the physical board.

## Service configuration

`config/studio.example.yaml` is a development configuration. `config/studio.systemd.example.yaml` shows the vehicle service using HTTP Event Protocol delivery over Ethernet.

The network section keeps physical transport separate from Event Protocol delivery:

```yaml
network:
  transport: ethernet
  event_protocol_transport: http_json
  runtime_endpoint: http://velvet-runtime.local:8765/v1/events
  request_timeout_seconds: 2.0
  bearer_token_file: /etc/velvet-audio/runtime.token
  max_response_bytes: 65536
```

HTTP requests use canonical Event Protocol JSON with deterministic `Idempotency-Key` and `X-Velvet-Event-ID` headers. Runtime must return a receipt identifier in JSON or a receipt header. A timeout, transport error, oversized response, or success without a receipt leaves the event in the durable ordered journal. A `409 Conflict` is treated as an acknowledged duplicate only when Runtime supplies the existing receipt.

Bearer tokens are read from the configured file for every publish, allowing token rotation without putting credentials in YAML or restarting the service.

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

Run the configured ALSA and Runtime transports until SIGINT or SIGTERM:

```bash
velvet-audio run \
  --config /etc/velvet-audio/studio.yaml \
  --runtime-mode configured
```

Runtime modes:

- `configured` follows `network.event_protocol_transport`.
- `stdout` emits canonical Event Protocol JSONL for development.
- `unavailable` intentionally rejects delivery so ordered events remain in the journal.

SIGINT and SIGTERM request an orderly shutdown. Capture closes first, stop events are generated in order, final delivery is attempted, and anything unacknowledged remains durable.

## Reference Runtime receiver

A small Runtime-side receiver is included for vehicle-LAN integration tests and durable-ingress development:

```bash
velvet-audio serve-runtime \
  --host 0.0.0.0 \
  --port 8765 \
  --database /var/lib/velvet-runtime-receiver/acknowledgements.sqlite3 \
  --bearer-token-file /etc/velvet-runtime-receiver/runtime.token
```

The receiver validates the envelope and idempotency headers, durably stores canonical event bytes in SQLite, returns `202` for a new event, and returns `409` with the original receipt for an exact replay. A receipt proves durable ingress acceptance, not completed downstream Court or organ processing.

Every accepted event also receives pending dispatch state in the same SQLite transaction. `SqliteIngressDispatchQueue` leases the oldest unprocessed event, blocks later events behind a live claim, recovers expired leases, and records the final downstream receipt only after processing succeeds.

`CourtRoutedIngressHandler` places a durable Court decision before routing. Approved events carry a bounded capability into the router. Durable denials finish with the Court denial receipt and never reach routing. Stable `runtime-dispatch-*` identities let Court and organs deduplicate retries across timeout, restart, and the final-commit crash gap.

`RuntimeDispatchWorker` turns that claim path into a continuous service loop with bounded retry backoff, lease renewal during slow Court or route calls, health events, graceful stop behavior, and evidence-backed quarantine. Generic repetition never authorizes a skip. Only explicitly classified permanent failures can become quarantine candidates, and they must repeat with the same fingerprint before a durable `runtime-quarantine-*` receipt advances the lane.

`build_runtime_dispatch_worker` assembles the queue and `CourtRoutedIngressHandler` around Runtime’s real Court and router implementations. It deliberately supplies no allow-all Court or placeholder route receipt.

Receiver deployment and trust boundaries are documented in `docs/runtime_receiver_deployment.md`. The HTTP sender contract is in `docs/runtime_http_contract.md`. Claim ordering, leases, migration, and Court routing are in `docs/runtime_ingress_dispatch.md`. Long-running worker operation and quarantine rules are in `docs/runtime_dispatch_worker.md`.

## systemd

The hardened audio unit is in `packaging/systemd/velvet-audio.service`. It validates configuration before launch, uses a dedicated `velvet-audio` account with supplementary ALSA access through the `audio` group, stores durable state in `/var/lib/velvet-audio`, and restarts on operational failure without looping on invalid configuration.

The reference Runtime receiver unit is in `packaging/systemd/velvet-runtime-receiver.service`. It has no device access and writes only to its managed acknowledgement state directory.

Audio-node installation steps are in `packaging/systemd/README.md`.

## Status

Foundation scaffold in progress on `foundation/initial-studio-tree`.
