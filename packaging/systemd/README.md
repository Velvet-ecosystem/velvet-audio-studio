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

## Create the service identity

```bash
sudo useradd --system --home /var/lib/velvet-audio --shell /usr/sbin/nologin velvet-audio
sudo usermod -a -G audio velvet-audio
```

## Install configuration and the unit

```bash
sudo install -d -o root -g velvet-audio -m 0750 /etc/velvet-audio
sudo install -o root -g velvet-audio -m 0640 \
  config/studio.systemd.example.yaml /etc/velvet-audio/studio.yaml
sudo install -o root -g root -m 0644 \
  packaging/systemd/velvet-audio.service /etc/systemd/system/velvet-audio.service
```

Edit `/etc/velvet-audio/studio.yaml` and replace the example Runtime endpoint. When bearer authentication is enabled, place only the token text in `/etc/velvet-audio/runtime.token`:

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

`SIGTERM` and `SIGINT` request an orderly stop. The service closes capture, emits capture and service stop events, attempts final Runtime delivery, and leaves any unacknowledged events in the durable journal.

## Hardware notes

Do not enable the unit until the pinned Raspberry Pi image passes `docs/hardware_acceptance.md`. The service intentionally finds the Octo by ALSA identity rather than card number. The unit does not use `PrivateDevices=true` because that would hide `/dev/snd` from ALSA.
