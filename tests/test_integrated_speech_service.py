import pytest

from velvet_audio_studio.integrated_speech_service import (
    IntegratedSpeechServiceError,
    _validate_args,
    build_parser,
)


def _args(*extra):
    return build_parser().parse_args(["--config", "studio.yaml", *extra])


def test_loopback_speech_ingress_does_not_require_bearer_token():
    _validate_args(_args("--host", "127.0.0.1"))
    _validate_args(_args("--host", "localhost"))
    _validate_args(_args("--host", "::1"))


def test_non_loopback_speech_ingress_requires_bearer_token():
    with pytest.raises(IntegratedSpeechServiceError, match="bearer-token-file"):
        _validate_args(_args("--host", "0.0.0.0"))


def test_non_loopback_speech_ingress_accepts_explicit_token_file():
    _validate_args(
        _args(
            "--host",
            "0.0.0.0",
            "--bearer-token-file",
            "/etc/velvet-audio/speech-ingress.token",
        )
    )


def test_invalid_poll_and_dispatch_limits_fail_closed():
    with pytest.raises(ValueError, match="poll-seconds"):
        _validate_args(_args("--poll-seconds", "0"))
    with pytest.raises(ValueError, match="max-dispatch-per-tick"):
        _validate_args(_args("--max-dispatch-per-tick", "0"))
