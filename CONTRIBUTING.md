# Contributing to Velvet Audio Studio

Thanks for helping improve Velvet Audio Studio.

The repository is intentionally modular and boundary-heavy. A contribution is successful when it adds capability without quietly creating a second owner for something the Studio, Runtime, Language, Event Protocol, or Receipts layer already owns.

## Before implementing

Perform a conflict sweep first.

Check the relevant modules, adapters, contracts, tests, and documentation for an existing implementation or ownership boundary before adding new machinery.

Prefer, in order:

1. reuse an existing capability;
2. extend an existing contract or adapter without breaking its owner;
3. add a new implementation behind an existing boundary;
4. create a new contract only when the earlier options do not fit.

Do not add duplicate booking, routing, retry, receipt, speech, or hardware-access paths simply because a new feature needs them.

## Ownership rules

- Audio Studio owns local audio capture/playback hardware access, routing, leases, acoustic delivery, and audio health.
- Velvet Language owns human-facing wording and expression strategy.
- Event Protocol owns shared transport contracts.
- Runtime/Court owns operational authority and capability enforcement.
- Velvet Receipts owns canonical evidence retention.
- Speech recognition and synthesis engines are implementations behind Studio boundaries; they do not gain authority from processing speech.

A new feature must not bypass those boundaries.

## Hardware access

Do not open the shared ALSA device directly from a feature, handmaiden, TTS engine, or application module.

Hardware-specific behavior belongs behind adapters. The Studio core should continue to work with simulated or replacement hardware through the same logical contracts.

Do not hard-code ALSA card numbers as stable identity. Discover devices by verified identity/capability and fail closed when the expected hardware is not present.

## Safety and priority

Priority is not authority.

A higher audio priority may affect Studio resource scheduling only within the documented lease/preemption rules. It must not be interpreted as permission to execute vehicle actions or bypass Runtime/Court.

Safety-relevant audio should remain deterministic, bounded, and testable. Equal or higher-priority leases must remain protected unless the contract explicitly changes and the change is reviewed across all affected boundaries.

## Privacy

Do not add raw cabin audio, private transcripts, spoken text, local model paths, credentials, or capability tokens to operational evidence unless a separate, explicit diagnostic design requires it.

Output evidence should prove what the audio organ did without becoming a second conversation archive.

Tests should use synthetic or clearly non-private fixtures.

## Hardware claims

Do not mark hardware `ACCEPTED` because it enumerates, imports, or works in CI.

Physical acceptance requires the evidence described in `docs/hardware_acceptance.md`, including real signal-path tests on the exact target OS/kernel/hardware combination.

If hardware has not been physically tested, say so plainly.

## Development setup

```bash
python -m pip install -e '.[dev]'
pytest
```

Optional speech dependencies:

```bash
python -m pip install -e '.[speech]'
python -m pip install -e '.[tts]'
```

Run configuration validation before attempting physical hardware:

```bash
velvet-audio validate-config --config config/studio.example.yaml
```

## Pull requests

Keep pull requests narrow enough that ownership and failure behavior can be reviewed.

A useful PR should describe:

- the existing boundary being reused or extended;
- why a new path is necessary if one was added;
- failure and degraded behavior;
- privacy implications;
- tests added or changed;
- whether any claim depends on unperformed physical acceptance.

CI must pass before merge. Hardware-dependent work should include simulated/unit coverage plus a clearly documented physical acceptance plan when the real device is not available in CI.

## Third-party material

Before vendoring models, schematics, PCB files, sample recordings, or other upstream assets, verify the applicable license and provenance.

See `docs/upstream_and_provenance.md`.
