from __future__ import annotations

import argparse

from velvet_audio_studio.adapters.alsa.capability_probe import probe_pcm
from velvet_audio_studio.diagnostics.probe import probe_json


def main() -> int:
    parser = argparse.ArgumentParser(prog="velvet-audio")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("probe", help="Inspect host and Audio Injector Octo presence")

    pcm_parser = subparsers.add_parser("probe-pcm", help="Inspect ALSA PCM capabilities")
    pcm_parser.add_argument("--device", required=True, help="ALSA device, for example hw:CARD=audioinjectoroc")
    pcm_parser.add_argument("--direction", required=True, choices=("playback", "capture"))

    args = parser.parse_args()
    if args.command == "probe":
        print(probe_json())
        return 0
    if args.command == "probe-pcm":
        print(probe_pcm(args.device, direction=args.direction).to_json())
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
