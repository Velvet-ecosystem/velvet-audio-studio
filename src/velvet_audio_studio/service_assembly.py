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
from velvet_audio_studio.adapters.audio_injector_octo.playback_factory import (
    OctoPlaybackResolution,
    resolve_octo_playback,
)
from velvet_audio_studio.capture.supervisor import CaptureSupervisor
from velvet_audio_studio.channel_registry import ChannelRegistry
from velvet_audio_studio.playback_engine import StudioSpeechPlaybackEngine
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
from velvet_audio_studio.session_manager import StudioSessionManager
from velvet_audio_studio.simulated.capture_source import (
    SimulatedCaptureSource,
    simulated_six_channel_frame,
)
from velvet_audio_studio.voice.config import (
    VoiceFrontEndServiceSettings,
    load_voice_frontend_settings,
)
from velvet_audio_studio.voice.front_end import LocalVoiceFrontEnd
from velvet_audio_studio.voice.output_service import LocalSpeechOutputService
from velvet_audio_studio.voice.piper_synthesizer import (
    PiperOfflineSynthesizer,
    PiperSynthesizerConfig,
)
from velvet_audio_studio.voice.speech_processor import LocalSpeechProcessor
from velvet_audio_studio.voice.synthesis import SpeechSynthesizer
from velvet_audio_studio.voice.transcribing_service_runner import (
    TranscribingAudioServiceRunner,
)
from velvet_audio_studio.voice.transcription import SpeechTranscriber
from velvet_audio_studio.voice.transcription_config import (
    TranscriptionServiceSettings,
    load_transcription_settings,
)
from velvet_audio_studio.voice.transcription_worker import BoundedTranscriptionWorker
from velvet_audio_studio.voice.tts_config import (
    TtsServiceSettings,
    load_tts_settings,
)
from velvet_audio_studio.voice.vosk_transcriber import (
    VoskOfflineTranscriber,
    VoskTranscriberConfig,
)
from velvet_audio_studio.voice.wake_gate import WakeNameGate


CaptureResolver = Callable[..., OctoCaptureResolution]
PlaybackResolver = Callable[..., OctoPlaybackResolution]
SpeechTranscriberFactory = Callable[[VoskTranscriberConfig], SpeechTranscriber]
SpeechSynthesizerFactory = Callable[[PiperSynthesizerConfig], SpeechSynthesizer]
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
    tts_settings: TtsServiceSettings
    speech_synthesizer: SpeechSynthesizer | None
    playback_resolution: OctoPlaybackResolution | None
    playback_engine: StudioSpeechPlaybackEngine | None
    speech_output_service: LocalSpeechOutputService | None
    backlog_supervisor: DurableBacklogSupervisor
    pipeline: ReliablePublishedCapturePipeline
    runner: AudioRunner
    capture_resolution: OctoCaptureResolution | None

    def describe(self) -> dict[str, object]:
        resolution = self.capture_resolution
        playback_resolution = self.playback_resolution
        network = self.config.network
        voice = self.voice_frontend_settings
        transcription = self.transcription_settings
        vosk = transcription.vosk
        tts = self.tts_settings
        piper = tts.piper
        playback = self.config.playback
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
            "tts_enabled": tts.enabled,
            "tts_engine": tts.engine,
            "tts_model_path": str(piper.model_path) if piper is not None else None,
            "tts_model_id": piper.model_path.stem if piper is not None else None,
            "tts_default_profile": tts.default_profile,
            "tts_use_cuda": piper.use_cuda if piper is not None else None,
            "playback_enabled": playback.enabled,
            "playback_source": playback.source,
            "playback_sample_rate_hz": playback.sample_rate_hz,
            "playback_sample_format": playback.sample_format.value,
            "playback_period_frames": playback.period_frames,
            "playback_default_output_channels": playback.default_output_channels,
            "playback_accepted": (
                playback_resolution.accepted
                if playback_resolution is not None
                else None
            ),
            "playback_alsa_device": (
                playback_resolution.config.device
                if playback_resolution is not None
                and playback_resolution.config is not None
                else None
            ),
            "playback_degraded_reasons": (
                playback_resolution.degraded_reasons
                if playback_resolution is not None
                else ()
            ),
            "speech_output_ready": self.speech_output_service is not None,
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

    def close_output(self) -> None:
        if self.speech_output_service is not None:
            self.speech_output_service.close()
            return
        if self.playback_engine is not None:
            self.playback_engine.close()
        if self.speech_synthesizer is not None:
            self.speech_synthesizer.close()


def build_audio_service(
    config: AudioServiceConfig,
    publisher: RuntimeEventPublisher,
    *,
    capture_resolver: CaptureResolver = resolve_octo_capture,
    playback_resolver: PlaybackResolver = resolve_octo_playback,
    transcriber_factory: SpeechTranscriberFactory = VoskOfflineTranscriber,
    synthesizer_factory: SpeechSynthesizerFactory = PiperOfflineSynthesizer,
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

    tts_settings = load_tts_settings(config.config_path)
    speech_synthesizer: SpeechSynthesizer | None = None
    if tts_settings.enabled:
        assert tts_settings.piper is not None
        speech_synthesizer = synthesizer_factory(tts_settings.piper)

    playback_resolution: OctoPlaybackResolution | None = None
    playback_engine: StudioSpeechPlaybackEngine | None = None
    speech_output_service: LocalSpeechOutputService | None = None
    if config.playback.enabled:
        playback_resolution = playback_resolver(
            identity_terms=config.playback.identity_terms,
            pcm_device=config.playback.pcm_device,
            plug=config.playback.use_plughw,
            sample_rate_hz=config.playback.sample_rate_hz,
            period_frames=config.playback.period_frames,
            sample_format=config.playback.sample_format,
        )
        sink = playback_resolution.require_sink()
        playback_engine = StudioSpeechPlaybackEngine(sink)
        if speech_synthesizer is not None:
            registry = ChannelRegistry(
                input_count=config.studio.input_channels,
                output_count=config.studio.output_channels,
            )
            speech_output_service = LocalSpeechOutputService(
                speech_synthesizer,
                StudioSessionManager(registry),
                playback_engine,
                default_output_channels=config.playback.default_output_channels,
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
        tts_settings=tts_settings,
        speech_synthesizer=speech_synthesizer,
        playback_resolution=playback_resolution,
        playback_engine=playback_engine,
        speech_output_service=speech_output_service,
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
