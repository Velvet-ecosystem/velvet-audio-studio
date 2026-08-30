"""Background HTTP/dispatch loop for speech ingress inside the primary Audio process."""

from __future__ import annotations

from threading import Event, Thread
from typing import Protocol

from velvet_audio_studio.runtime.ingress_dispatch import DurableIngressDispatcher


class SpeechIngressServer(Protocol):
    timeout: float

    def handle_request(self) -> None:
        ...

    def server_close(self) -> None:
        ...


class SpeechIngressBackgroundError(RuntimeError):
    """Raised when the integrated speech ingress worker cannot stop cleanly."""


class SpeechIngressBackgroundRunner:
    """Run accept and dispatch work without creating another playback owner."""

    def __init__(
        self,
        server: SpeechIngressServer,
        dispatcher: DurableIngressDispatcher,
        *,
        poll_seconds: float = 0.25,
        max_dispatch_per_tick: int = 16,
        thread_name: str = "velvet-speech-ingress",
    ) -> None:
        if poll_seconds <= 0:
            raise ValueError("speech ingress poll_seconds must be positive")
        if max_dispatch_per_tick <= 0:
            raise ValueError("max_dispatch_per_tick must be positive")
        if not thread_name.strip():
            raise ValueError("speech ingress thread_name cannot be empty")
        self.server = server
        self.dispatcher = dispatcher
        self.poll_seconds = float(poll_seconds)
        self.max_dispatch_per_tick = int(max_dispatch_per_tick)
        self.thread_name = thread_name.strip()
        self.server.timeout = self.poll_seconds
        self._stop_requested = Event()
        self._stopped = Event()
        self._thread: Thread | None = None
        self._failure: BaseException | None = None

    @property
    def failure(self) -> BaseException | None:
        return self._failure

    @property
    def is_running(self) -> bool:
        thread = self._thread
        return thread is not None and thread.is_alive()

    @property
    def has_failed(self) -> bool:
        return self._failure is not None

    def start(self) -> None:
        if self._thread is not None:
            raise SpeechIngressBackgroundError("speech ingress runner cannot be restarted")
        self._thread = Thread(
            target=self._run,
            name=self.thread_name,
            daemon=False,
        )
        self._thread.start()

    def stop(self, *, timeout_seconds: float = 5.0) -> None:
        if timeout_seconds <= 0:
            raise ValueError("speech ingress stop timeout must be positive")
        self._stop_requested.set()
        thread = self._thread
        if thread is None:
            self.server.server_close()
            self._stopped.set()
            return
        thread.join(timeout_seconds)
        if thread.is_alive():
            raise SpeechIngressBackgroundError(
                "speech ingress background thread did not stop within timeout"
            )

    def wait_stopped(self, timeout_seconds: float) -> bool:
        if timeout_seconds < 0:
            raise ValueError("timeout_seconds cannot be negative")
        return self._stopped.wait(timeout_seconds)

    def raise_if_failed(self) -> None:
        if self._failure is not None:
            raise SpeechIngressBackgroundError(
                f"speech ingress background loop failed: {type(self._failure).__name__}"
            ) from self._failure

    def _run(self) -> None:
        try:
            while not self._stop_requested.is_set():
                self.dispatcher.drain_available(
                    max_events=self.max_dispatch_per_tick
                )
                if self._stop_requested.is_set():
                    break
                self.server.handle_request()
                self.dispatcher.drain_available(
                    max_events=self.max_dispatch_per_tick
                )
        except BaseException as exc:
            self._failure = exc
            self._stop_requested.set()
        finally:
            try:
                self.server.server_close()
            finally:
                self._stopped.set()
