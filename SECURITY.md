# Security Policy

Velvet Audio Studio handles local microphones, speech processing, network event delivery, durable state, and shared audio hardware. Security reports are welcome, especially when a flaw could cross one of those trust boundaries.

## What to report privately

Please avoid publishing exploit details, credentials, private audio, transcripts, tokens, or other sensitive evidence in a public issue.

Examples that should be treated as security-sensitive include:

- bypassing Event Protocol or Runtime/Court authority boundaries;
- causing untrusted speech input to gain command or actuation authority;
- escaping the Studio lease boundary to seize audio hardware;
- leaking raw cabin audio or unmatched transcript text into Runtime events or receipts;
- exposing bearer tokens or protected model/state files;
- replay/idempotency flaws that can turn old evidence into renewed authority;
- path or configuration handling that permits unintended file access;
- malformed network input that causes unsafe resource exhaustion or persistent denial of service.

## Reporting

If GitHub private vulnerability reporting is available for this repository, use the repository **Security** tab and submit a private report.

If private reporting is not available, open a minimal public issue that states only that you have a security concern and asks the maintainers for a private channel. Do not include exploit steps, secrets, private recordings, or sensitive logs in that issue.

## Include when possible

A useful private report contains:

- affected commit/version;
- affected component or boundary;
- reproduction steps using synthetic/non-private data;
- expected versus observed behavior;
- likely impact;
- whether the issue requires physical hardware;
- suggested mitigation, if known.

## Safety boundary

Audio Studio controls audio resources, not vehicle authority.

A security fix must not solve an audio problem by silently moving command, actuation, identity, or Court authority into this repository. Preserve the ownership boundaries documented in the README and architecture docs.

## Public disclosure

Please allow maintainers time to reproduce, patch, test, and prepare a release before public disclosure of a vulnerability. Once a fix is available, the project can coordinate an appropriate public explanation that does not expose private user data.
