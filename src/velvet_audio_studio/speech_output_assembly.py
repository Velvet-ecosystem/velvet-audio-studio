"""Assemble Audio Studio's speech-output organ without opening capture hardware."""

from __future__ import annotations

from dataclasses import dataclass
from time import monotonic_ns
from typing import Callable

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
from velvet_audio_studio.runtime.output_evidence import AudioOutputEvidenceEmitter
from velvet_audio_studio.runtime.publisher import RuntimeEventPublisher
from velvet_audio_studio.runtime.retry_journal import JsonlRetryJournal
from velvet_audio_studio.service_config import AudioServiceConfig
from velvet_audio_studio.session_manager import StudioSessionManager
from velvet_audio_studio.voice.output_service import LocalSpeechOutputService
from velvet_audio_studio.voice.piper_synthesizer import (
    PiperOfflineSynthesizer,
    PiperSynthesizerConfig,
)
from velvet_audio_studio.voice.synthesis import SpeechSynthesizer
from velvet_audio_studio.voice.tts_config import (
    TtsServiceSettings,
    load_tts_settings,
)


class SpeechOutputAssemblyError(RuntimeError):
    """Raised when the configured speech-only output path cannot be assembled."""


PlaybackResolver = Callable[..., OctoPlaybackResolution]
SpeechSynthesizerFactory = Callable[[PiperSynthesizerConfig], SpeechSynthesizer]


@dataclass(frozen=True)
class SpeechOutputAssembly:
    config: AudioServiceConfig
    publisher: RuntimeEventPublisher
    tts_settings: TtsServiceSettings
    speech_synthesizer: SpeechSynthesizer
    playback_resolution: OctoPlaybackResolution
    playback_engine: StudioSpeechPlaybackEngine
    journal: JsonlRetryJournal
    retry_queue: DurableOrderedRetryQueue
    capture_supervisor: CaptureSupervisor
    backlog_supervisor: DurableBacklogSupervisor
    pipeline: ReliablePublishedCapturePipeline
    output_evidence_emitter: AudioOutputEvidenceEmitter
    speech_output_service: LocalSpeechOutputService

    def close(self) -> None:
        self.speech_output_service.close()


def build_speech_output_service(
    config: AudioServiceConfig,
    publisher: RuntimeEventPublisher,
    *,
    playback_resolver: PlaybackResolver = resolve_octo_playback,
    synthesizer_factory: SpeechSynthesizerFactory = PiperOfflineSynthesizer,
    clock_ns: Callable[[], int] = monotonic_ns,
) -> SpeechOutputAssembly:
    """Build only the TTS/playback path used by Runtime speech ingress.

    This deliberately does not resolve capture hardware, instantiate Vosk, start
    a capture runner, or activate the voice front end. Output evidence still uses
    the existing durable Audio -> Runtime publication pipeline.
    """

    tts_settings = load_tts_settings(config.config_path)
    if not tts_settings.enabled or tts_settings.piper is None:
        raise SpeechOutputAssemblyError(
            "Runtime speech ingress requires tts.enabled with a local Piper model"
        )
    if not config.playback.enabled:
        raise SpeechOutputAssemblyError(
            "Runtime speech ingress requires playback.enabled"
        )

    synthesizer: SpeechSynthesizer | None = None
    playback_engine: StudioSpeechPlaybackEngine | None = None
    try:
        synthesizer = synthesizer_factory(tts_settings.piper)
        playback_resolution = playback_resolver(
            identity_terms=config.playback.identity_terms,
            pcm_device=config.playback.pcm_device,
            plug=config.playback.use_plughw,
            sample_rate_hz=config.playback.sample_rate_hz,
            period_frames=config.playback.period_frames,
            sample_format=config.playback.sample_format,
        )
        playback_engine = StudioSpeechPlaybackEngine(
            playback_resolution.require_sink()
        )

        journal = JsonlRetryJournal(config.capture.retry_journal)
        retry_queue = DurableOrderedRetryQueue(
            journal,
            max_pending=config.capture.max_pending_runtime_events,
        )
        capture_supervisor = CaptureSupervisor()
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
            voice_frontend=None,
        )
        output_evidence_emitter = AudioOutputEvidenceEmitter(
            node_id=config.studio.node_id,
            publish_events=pipeline.publish_events,
            clock_ns=clock_ns,
        )
        registry = ChannelRegistry(
            input_count=config.studio.input_channels,
            output_count=config.studio.output_channels,
        )
        speech_output_service = LocalSpeechOutputService(
            synthesizer,
            StudioSessionManager(registry),
            playback_engine,
            default_output_channels=config.playback.default_output_channels,
            evidence_emitter=output_evidence_emitter,
        )
        return SpeechOutputAssembly(
            config=config,
            publisher=publisher,
            tts_settings=tts_settings,
            speech_synthesizer=synthesizer,
            playback_resolution=playback_resolution,
            playback_engine=playback_engine,
            journal=journal,
            retry_queue=retry_queue,
            capture_supervisor=capture_supervisor,
            backlog_supervisor=backlog_supervisor,
            pipeline=pipeline,
            output_evidence_emitter=output_evidence_emitter,
            speech_output_service=speech_output_service,
        )
    except Exception:
        if playback_engine is not None:
            playback_engine.close()
        if synthesizer is not None:
            synthesizer.close()
        raise
