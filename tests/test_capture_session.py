from velvet_audio_studio.capture.microphone_capture import analyze_capture
from velvet_audio_studio.capture.session import CaptureSession, CaptureSessionState


def packet(samples: tuple[float, ...], *, stale: bool = False):
    captured = 1_000_000_000
    observed = 1_400_000_000 if stale else 1_010_000_000
    return analyze_capture(
        samples,
        captured_at_monotonic_ns=captured,
        observed_at_monotonic_ns=observed,
    )


def test_session_starts_activates_degrades_recovers_and_stops() -> None:
    session = CaptureSession(recovery_packets_required=2)

    started = session.start()
    assert started.event == "audio.capture.starting"
    assert session.state is CaptureSessionState.STARTING

    active = session.observe(packet((0.1, 0.0, 0.0, 0.0, 0.0, 0.0)))
    assert active[0].event == "audio.capture.active"
    assert session.state is CaptureSessionState.ACTIVE

    degraded = session.observe(packet((0.1, 0.0, 0.0, 0.0, 0.0, 0.0), stale=True))
    assert degraded[0].event == "audio.capture.degraded"
    assert session.state is CaptureSessionState.DEGRADED

    assert session.observe(packet((0.2, 0.0, 0.0, 0.0, 0.0, 0.0))) == ()
    recovered = session.observe(packet((0.2, 0.0, 0.0, 0.0, 0.0, 0.0)))
    assert recovered[0].event == "audio.capture.recovered"
    assert session.state is CaptureSessionState.ACTIVE

    stopped = session.stop()
    assert stopped.event == "audio.capture.stopped"
    assert session.state is CaptureSessionState.STOPPED


def test_repeated_bad_packets_do_not_spam_degraded_transitions() -> None:
    session = CaptureSession()
    session.start()

    first = session.observe(packet((0.0, 0.0, 0.0, 0.0, 0.0, 0.0), stale=True))
    second = session.observe(packet((0.0, 0.0, 0.0, 0.0, 0.0, 0.0), stale=True))

    assert first[0].event == "audio.capture.degraded"
    assert second == ()


def test_invalid_lifecycle_actions_are_rejected() -> None:
    session = CaptureSession()

    try:
        session.stop()
    except RuntimeError:
        pass
    else:
        raise AssertionError("stopping an idle session must fail")

    session.start()
    try:
        session.start()
    except RuntimeError:
        pass
    else:
        raise AssertionError("starting an active session must fail")
