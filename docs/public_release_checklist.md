# Public Release Checklist

Use this checklist before changing repository visibility from private to public.

## Repository surface

- [x] README describes implemented capability and unfinished physical acceptance truthfully.
- [x] GPL-3.0 license file is present.
- [x] Contribution boundaries are documented.
- [x] Security reporting guidance is documented.
- [x] Upstream hardware/software provenance is documented without vendoring third-party assets.
- [x] Example configuration does not contain live credentials.
- [x] `.env` is ignored.
- [x] Vosk and Piper models are not committed to the repository.

## Technical posture

- [x] CI covers the software path.
- [x] Vosk and Piper remain optional dependencies.
- [x] Physical playback is disabled by default.
- [x] README clearly distinguishes CI success from Raspberry Pi/Octo hardware acceptance.
- [x] Known Octo/kernel risk is documented.
- [x] Speech events remain authority-free.
- [x] Output evidence excludes spoken text and raw PCM.

## Final visibility change

Immediately before switching visibility:

1. Confirm `main` contains the public-release cleanup PR.
2. Confirm the latest `main` CI is green.
3. Re-run a repository search for accidental credentials, tokens, private recordings, model files, or local-only artifacts.
4. Review GitHub repository description/topics and branch rules for public use.
5. Change visibility to public.
6. Open the public repository in a logged-out/incognito view and verify README links render correctly.

## Hardware status at publication

Publication does **not** mean the physical Raspberry Pi 3 + Audio Injector Octo path is accepted.

At initial public release the intended status remains:

- software architecture and CI: implemented/tested;
- physical Pi/Octo/Vosk/Piper acceptance: pending evidence on the target hardware;
- physical playback: disabled by default until acceptance.

Update this checklist and the README when that hardware state changes.
