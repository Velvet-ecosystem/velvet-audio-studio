from threading import Event
from time import sleep

import pytest

from velvet_audio_studio.runtime.speech_ingress_background import (
    SpeechIngressBackgroundError,
    SpeechIngressBackgroundRunner,
)


class FakeServer:
    def __init__(self):
        self.timeout = 0.0
        self.requests = 0
        self.closed = 0
        self.request_seen = Event()

    def handle_request(self):
        self.requests += 1
        self.request_seen.set()
        sleep(min(self.timeout, 0.01))

    def server_close(self):
        self.closed += 1


class FakeDispatcher:
    def __init__(self, failure=None):
        self.calls = 0
        self.failure = failure

    def drain_available(self, *, max_events):
        assert max_events > 0
        self.calls += 1
        if self.failure is not None:
            raise self.failure
        return ()


def test_background_runner_starts_and_stops_without_owning_playback():
    server = FakeServer()
    dispatcher = FakeDispatcher()
    runner = SpeechIngressBackgroundRunner(
        server,
        dispatcher,
        poll_seconds=0.01,
        max_dispatch_per_tick=4,
    )

    runner.start()
    assert server.request_seen.wait(1.0)
    runner.stop(timeout_seconds=1.0)

    assert runner.is_running is False
    assert runner.has_failed is False
    assert dispatcher.calls >= 1
    assert server.closed == 1


def test_background_failure_stops_loop_and_is_reported():
    server = FakeServer()
    dispatcher = FakeDispatcher(RuntimeError("dispatch failure"))
    runner = SpeechIngressBackgroundRunner(
        server,
        dispatcher,
        poll_seconds=0.01,
    )

    runner.start()
    assert runner.wait_stopped(1.0)

    assert runner.has_failed is True
    assert server.closed == 1
    with pytest.raises(SpeechIngressBackgroundError, match="RuntimeError"):
        runner.raise_if_failed()


def test_background_runner_rejects_restart():
    server = FakeServer()
    runner = SpeechIngressBackgroundRunner(
        server,
        FakeDispatcher(),
        poll_seconds=0.01,
    )

    runner.start()
    assert server.request_seen.wait(1.0)
    runner.stop(timeout_seconds=1.0)

    with pytest.raises(SpeechIngressBackgroundError, match="cannot be restarted"):
        runner.start()
