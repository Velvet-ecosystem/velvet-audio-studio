# systemd deployment

This unit runs the audio studio as a dedicated `velvet-audio` service account, validates configuration before launch, writes durable Runtime backlog state under `/var/lib/velvet-audio`, and receives ALSA access through the host `audio` group.

## Install the application

```bash
sudo install -d -o root -g root -m 0755 /opt/velvet-audio-studio
sudo cp -a . /opt/velvet-audio-studio/
cd /opt/velvet-audio-studio
sudo python3.11 -m venv .venv
sudo .venv/bin/pip install --upgrade pip
sudo .venv/bin/pip install .
```

Install optional local speech dependencies only on nodes that need them:

```bash
sudo /opt/velvet-audio-studio/.venv/bin/pip install '/opt/velvet-audio-studio[speech]'
sudo /opt/velvet-audio-studio/.venv/bin/pip install '/opt/velvet-audio-studio[tts]'
```

## Create the service identity

```bash
sudo useradd --system --home /var/lib/velvet-audio --shell /usr/sbin/nologin velvet-audio
sudo usermod -a -G audio velvet-audio
```

## Provision a protected Vosk model

Acquire and verify the model out of band. Install the extracted directory as root-owned shared data:

```bash
sudo install -d -o root -g root -m 0755 /usr/share/velvet-audio/models
sudo cp -a vosk-model-small-en-us-0.15 /usr/share/velvet-audio/models/
sudo chown -R root:root /usr/share/velvet-audio/models/vosk-model-small-en-us-0.15
sudo chmod -R a-w /usr/share/velvet-audio/models/vosk-model-small-en-us-0.15
```

Record the model checksum, source, license, and extraction receipt. The `velvet-audio` account should be able to read the model but must not be able to modify it.

## Provision a protected Piper voice

Acquire and verify the ONNX voice and matching JSON config out of band:

```bash
sudo install -d -o root -g root -m 0755 /usr/share/velvet-audio/voices
sudo install -o root -g root -m 0444 velvet.onnx /usr/share/velvet-audio/voices/velvet.onnx
sudo install -o root -g root -m 0444 velvet.onnx.json /usr/share/velvet-audio/voices/velvet.onnx.json
```

Record the model/config checksums, source, license, Piper version, and architecture evidence. The service must not download or modify voice files.

## Install configuration and the unit

```bash
sudo install -d -o root -g velvet-audio -m 0750 /etc/velvet-audio
sudo install -o root -g velvet-audio -m 0640 \
  config/studio.systemd.example.yaml /etc/velvet-audio/studio.yaml
sudo install -o root -g root -m 0644 \
  packaging/systemd/velvet-audio.service /etc/systemd/system/velvet-audio.service
```

Edit `/etc/velvet-audio/studio.yaml`, replace the example Runtime endpoint, and enable transcription, TTS, or playback only after their required local assets and Pi acceptance evidence exist. Playback stays disabled by default. Before changing `playback.enabled` to `true`, confirm the identity-based ALSA probe accepts the eight-channel output endpoint, configured sample format/rate/period, and intended default output slot.

When bearer authentication is enabled, place only the token text in `/etc/velvet-audio/runtime.token`:

```bash
sudo install -o root -g velvet-audio -m 0640 /dev/null /etc/velvet-audio/runtime.token
sudoedit /etc/velvet-audio/runtime.token
```

The token is read for each HTTP publish so it can be rotated without storing it in YAML.

## Validate and start

```bash
sudo -u velvet-audio -g velvet-audio \
  /opt/velvet-audio-studio/.venv/bin/velvet-audio \
  validate-config --config /etc/velvet-audio/studio.yaml

sudo systemctl daemon-reload
sudo systemctl enable --now velvet-audio.service
systemctl status velvet-audio.service
journalctl -u velvet-audio.service -f
```

`SIGTERM` and `SIGINT` request an orderly stop. The service closes capture, cancels active local utterances, drains the bounded transcription worker, closes the Studio-owned playback stream and lazy synthesizer when present, emits stop events, attempts final Runtime delivery, and leaves any unacknowledged events in the durable journal.

## Hardware notes

Do not enable the unit until the pinned Raspberry Pi image passes `docs/hardware_acceptance.md`. The service intentionally finds the Octo by ALSA identity rather than card number. Capture and playback each validate the actual exposed PCM capability. The unit does not use `PrivateDevices=true` because that would hide `/dev/snd` from ALSA.

An x86 CI import of Vosk or Piper does not prove Raspberry Pi compatibility. Record the exact packages, model/voice load, memory, real-time factor, temperature, wake-name results, eight-channel playback routing, and concurrent capture/playback behavior on the physical node before accepting speech I/O.
