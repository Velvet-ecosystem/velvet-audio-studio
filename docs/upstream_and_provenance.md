# Upstream and Provenance

Velvet Audio Studio is designed to sit on top of existing open hardware and open-source speech tooling without blurring ownership or license boundaries.

This page records the main upstream lineages used by the current implementation. It is a provenance map, not a replacement for the license files and notices supplied by each upstream project.

## Audio Injector Octo

Current reference hardware:

- Audio Injector Octo project: https://github.com/Audio-Injector/Octo
- Octo design template: https://github.com/Audio-Injector/AudioInjector.Octo.template
- Octo RCA breakout reference: https://github.com/Audio-Injector/AudioInjector.Octo.RCA

Audio Injector community material also describes the Octo template as a starting point for custom designs and points users to the KiCad/schematic material.

Velvet Audio Studio currently **links to** these upstream hardware references. It does not vendor or claim ownership of the Audio Injector schematics, PCB files, drivers, overlays, or board design.

Before copying any upstream hardware-design asset into a Velvet repository, verify the license that applies to that specific asset and preserve its attribution and notices. A public URL or open repository is not, by itself, permission to relicense a design.

The Octo is treated as a reference organ and initial hardware adapter. Future Velvet-native hardware should preserve the Studio capability contracts rather than copying undocumented implementation details blindly.

## Piper TTS

Current upstream:

- Piper: https://github.com/OHF-Voice/piper1-gpl
- Python package: `piper-tts`

Audio Studio uses Piper only through the optional `tts` dependency declared in `pyproject.toml`. The service loads a local ONNX voice and configuration supplied by deployment; it does not download a voice during normal startup.

Piper's engine/package license and a voice model's license are separate provenance questions. Before deploying or redistributing a voice, record at minimum:

- model identifier
- source URL or source package
- model/config checksums
- model size
- license and attribution requirements
- date acquired

Do not assume that a voice model inherits the engine's license.

## Vosk

Current upstream:

- Vosk API: https://github.com/alphacep/vosk-api
- Python package: `vosk`

Audio Studio uses Vosk through the optional `speech` dependency declared in `pyproject.toml`. Recognition models are provisioned locally and are not bundled with this repository.

Before deploying or redistributing a Vosk model, record its source, identifier, checksum, size, and license separately from the Vosk API package.

## Python dependencies

The authoritative dependency ranges are in `pyproject.toml`.

At release or deployment time, generate or record a dependency inventory from the resolved environment. Do not treat the dependency declarations in this repository as a complete third-party license report for a built operating-system image.

## Models and generated audio

This repository does not include a Vosk recognition model or Piper voice model.

Local models belong in protected deployment storage, not in Git history. Deployment documentation uses `/usr/share/velvet-audio/...` examples so models can be root-owned and read-only to the service.

If future tests require sample audio or model fixtures, prefer synthetic/minimal fixtures with clear provenance. Do not commit private cabin recordings, user transcripts, or third-party voice-model files merely to make a test convenient.

## Hardware acceptance evidence

Hardware acceptance records should identify the exact upstream and deployment artifacts used:

- board model/revision
- operating-system image
- kernel and overlays
- driver/package provenance
- Piper/Vosk package versions and install source
- model identifiers, hashes, and licenses
- relevant upstream patches or local modifications

That evidence lets a working node be reproduced without quietly turning upstream material into Velvet-owned material.

## Velvet ownership boundary

Velvet owns the code and documentation authored in this repository under the repository license. Third-party software, models, schematics, PCB designs, kernels, drivers, and other upstream assets retain their own licenses and ownership.

When in doubt: link first, record provenance, verify the license, then decide whether redistribution is appropriate.
