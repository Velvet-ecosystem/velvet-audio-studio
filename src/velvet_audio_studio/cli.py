from __future__ import annotations

import argparse

from velvet_audio_studio.diagnostics.probe import probe_json


def main() -> int:
    parser = argparse.ArgumentParser(prog="velvet-audio")
    parser.add_argument("command", choices=("probe",))
    args = parser.parse_args()
    if args.command == "probe":
        print(probe_json())
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
