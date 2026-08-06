"""Assemble the configured Velvet audio service from explicit boundaries."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from time import monotonic_ns, sleep
from typing import Callable

from velvet_audio_studio.adapters.audio_injector_octo.capture_factory import (
    OctoCaptureResolution,
    resolve_octo_capture,
)
from velvet_audio_studio.capture.supervisor import CaptureSupervisor
from velvet_audio_studio.runtime.backlog_supervisor import DurableBacklogSupervisor
from velvet_audio_studio.runtime.capture_pipeline import ReliablePublishedCapturePipeline
from velvet_audio_studio.runtime.durable_retry_queue import DurableOrderedRetryQueue
from velvet_audio_studio.runtime.publisher import RuntimeEventPublisher
from velvet_audio_studio.runtime.retry_journal import JsonlRetryJournal
from velvet_audio_studio.runtime.service_runner import (
    CaptureFrame,
    CaptureSource,
    ReliableAudioServiceRunner,
)
from velvet_audio_studio.service_config import AudioServiceConfig
from velvet_audio_studio.simulated.capture_source import (
    SimulatedCaptureSource,
    simulated_six_channel_frame,
)
from velvet_audio_studio.voice.config import (
    VoiceFrontEndServiceSettings,
    load_voice_frontend_settings,
)
from velvet_audio_studio.voice.front_end import LocalVoiceFrontEnd
from velvet_audio_studio.voice.speech_processor import LocalSpeechProcessor
from velvet_audio_studio.voice.transcribing_service_runner import (
    TranscribingAudioServiceRunner,
)
from velvet_audio_studio.voice.transcription import SpeechTranscriber
from velvet_audio_studio.voice.transcription_config import (
    TranscriptionServiceSettings,
    load_transcription_settings,
)
from velvet_audio_studio.voice.transcription_worker import BoundedTranscriptionWorker
from velvet_audio_studio.voice.vosk_transcriber import (
    VoskOfflineTranscriber,
    VoskTranscriberConfig,
)
from velvet_audio_studio.voice.wake_gate import WakeNameGate


CaptureResolver = Callable[..., OctoCaptureResolution]
SpeechTranscriberFactory = Callable[[VoskTranscriberConfig], SpeechTranscriber]
AudioRunner = ReliableAudioServiceRunner | TranscribingAudioServiceRunner


@dataclass(frozen=True)
class AudioServiceAssembly:
    config: AudioServiceConfig
    capture_source: CaptureSource
    publisher: RuntimeEventPublisher
    journal: JsonlRetryJournal
    retry_queue: DurableOrderedRetryQueue
    capture_supervisor: CaptureSupervisor
    voice_frontend_settings: VoiceFrontEndServiceSettings
    voice_frontend: LocalVoiceFrontEnd | None
    transcription_settings: TranscriptionServiceSettings
    speech_processor: LocalSpeechProcessor | None
    transcription_worker: BoundedTranscriptionWorker | None
    backlog_supervisor: DurableBacklogSupervisor
    pipeline: ReliablePublishedCapturePipeline
    runner: AudioRunner
    capture_resolution: OctoCaptureResolution | None

    def describe(self) -> dict[str, object]:
        resolution = self.capture_resolution
        network = self.config.network
        voice = self.voice_frontend_settings
        transcription = self.transcription_settings
        vosk = transcription.vosk
        return {
            "node_id": self.config.studio.node_id,
            "capture_source": self.config.capture.source,
            "sample_rate_hz": self.config.capture.sample_rate_hz,
            "sample_format": self.config.capture.sample_format.value,
            "period_frames": self.config.capture.period_frames,
            "retry_journal": str(self.config.capture.retry_journal),
            "voice_frontend_enabled": voice.enabled,
            "voice_activation_rms": voice.frontend.vad.activation_rms,
            "voice_deactivation_rms": voice.frontend.vad.deactivation_rms,
            "voice_activation_packets": voice.frontend.vad.activation_packets,
            "voice_release_packets": voice.frontend.vad.release_packets,
            "voice_pre_roll_ms": voice.frontend.utterance.pre_roll_ms,
            "voice_minimum_utterance_ms": voice.frontend.utterance.minimum_duration_ms,
            "voice_maximum_utterance_ms": voice.frontend.utterance.maximum_duration_ms,
            "transcription_enabled": transcription.enabled,
            "transcription_engine": transcription.engine,
            "transcription_model_path": str(vosk.model_path) if vosk is not None else None,
            "transcription_model_id": vosk.model_path.name if vosk is not None else None,
            "transcription_sample_rate_hz": (
                vosk.recognizer_sample_rate_hz if vosk is not None else None
            ),
            "transcription_queue_capacity": transcription.queue_capacity,
            "transcription_wake_names": transcription.wake.names,
            "network_transport": network.transport,
            "event_protocol_transport": network.event_protocol_transport,
            "runtime_endpoint": network.runtime_endpoint,
            "request_timeout_seconds": network.request_timeout_seconds,
            "bearer_token_file": (
                str(network.bearer_token_file)
                if network.bearer_token_file is not None
                else None
            ),
            "octo_accepted": resolution.accepted if resolution is not None else None,
            "alsa_device": (
                resolution.config.device
                if resolution is not None and resolution.config is not None
                else None
            ),
            "degraded_reasons": (
                resolution.degraded_reasons if resolution is not None else ()
            ),
        }


def build_audio_service(
    config: AudioServiceConfig,
    publisher: RuntimeEventPublisher,
    *,
    capture_resolver: CaptureResolver = resolve_octo_capture,
    transcriber_factory: SpeechTranscriberFactory = VoskOfflineTranscriber,
    simulated_items: Iterable[CaptureFrame | None | Exception] | None = None,
    clock_ns: Callable[[], int] = monotonic_ns,
    sleeper: Callable[[float], None] = sleep,
) -> AudioServiceAssembly:
    capture_source, resolution = _build_capture_source(
        config,
        capture_resolver=capture_resolver,
        simulated_items=simulated_items,
        clock_ns=clock_ns,
    )

    journal = JsonlRetryJournal(config.capture.retry_journal)
    retry_queue = DurableOrderedRetryQueue(
        journal,
        max_pending=config.capture.max_pending_runtime_events,
    )
    capture_supervisor = CaptureSupervisor()
    voice_settings = load_voice_frontend_settings(config.config_path)
    voice_frontend = (
        LocalVoiceFrontEnd(voice_settings.frontend)
        if voice_settings.enabled
        else None
    )
    transcription_settings = load_transcription_settings(config.config_path)
    if transcription_settings.enabled and voice_frontend is None:
        raise ValueError("transcription requires voice_frontend.enabled to be true")

    speech_processor: LocalSpeechProcessor | None = None
    transcription_worker: BoundedTranscriptionWorker | None = None
    if transcription_settings.enabled:
        assert transcription_settings.vosk is not None
        transcriber = transcriber_factory(transcription_settings.vosk)
        speech_processor = LocalSpeechProcessor(
            transcriber,
            WakeNameGate(transcription_settings.wake),
        )
        transcription_worker = BoundedTranscriptionWorker(
            speech_processor,
            queue_capacity=transcription_settings.queue_capacity,
            clock_ns=clock_ns,
        )

    backlog_supervisor = DurableBacklogSupervisor(
        retry_queue,
        capacity_warning_ratio=config.capture.backlog_warning_ratio,
        max_age_ms=config.capture.backlog_max_age_ms,
    )
    pipeline = ReliablePublishedCapturePipeline(
        capture_supervisor,
        publisher,
        retry_queue,
        backlog_supervisor,
        voice_frontend=voice_frontend,
    )
    base_runner = ReliableAudioServiceRunner(
        pipeline,
        capture_source,
        heartbeat_interval_ms=config.capture.heartbeat_interval_ms,
        idle_poll_seconds=config.capture.idle_poll_seconds,
        clock_ns=clock_ns,
        sleeper=sleeper,
    )
    runner: AudioRunner = base_runner
    if transcription_worker is not None:
        runner = TranscribingAudioServiceRunner(
            base_runner,
            transcription_worker,
            worker_stop_timeout_seconds=(
                transcription_settings.worker_stop_timeout_seconds
            ),
        )
    return AudioServiceAssembly(
        config=config,
        capture_source=capture_source,
        publisher=publisher,
        journal=journal,
        retry_queue=retry_queue,
        capture_supervisor=capture_supervisor,
        voice_frontend_settings=voice_settings,
        voice_frontend=voice_frontend,
        transcription_settings=transcription_settings,
        speech_processor=speech_processor,
        transcription_worker=transcription_worker,
        backlog_supervisor=backlog_supervisor,
        pipeline=pipeline,
        runner=runner,
        capture_resolution=resolution,
    )


def _build_capture_source(
    config: AudioServiceConfig,
    *,
    capture_resolver: CaptureResolver,
    simulated_items: Iterable[CaptureFrame | None | Exception] | None,
    clock_ns: Callable[[], int],
) -> tuple[CaptureSource, OctoCaptureResolution | None]:
    capture = config.capture
    if capture.source == "simulated":
        items = tuple(simulated_items) if simulated_items is not None else (
            _default_simulated_frame(sample_rate_hz=capture.sample_rate_hz),
        )
        return SimulatedCaptureSource(items, clock_ns=clock_ns), None

    resolution = capture_resolver(
        identity_terms=capture.identity_terms,
        pcm_device=capture.pcm_device,
        plug=capture.use_plughw,
        sample_rate_hz=capture.sample_rate_hz,
        period_frames=capture.period_frames,
        sample_format=capture.sample_format,
    )
    source = resolution.require_source(clock_ns=clock_ns)
    return source, resolution


def _default_simulated_frame(*, sample_rate_hz: int) -> CaptureFrame:
    return simulated_six_channel_frame(
        (
            0.20,
            0.10,
            0.04,
            0.03,
            0.08,
            0.00,
            -0.20,
            -0.10,
            -0.04,
            -0.03,
            -0.08,
            0.00,
        ),
        captured_at_monotonic_ns=0,
        sample_rate_hz=sample_rate_hz,
    )
