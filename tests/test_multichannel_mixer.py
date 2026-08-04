import pytest

from velvet_audio_studio.engine.multichannel_mixer import MixInput, MultichannelMixer


def test_mixer_routes_mono_sources_into_interleaved_eight_channel_output() -> None:
    mixer = MultichannelMixer(output_channels=8)

    output = mixer.mix(
        [
            MixInput(samples=(0.25, 0.5), output_channels=(0, 1)),
            MixInput(samples=(0.5, -0.5), output_channels=(4,), gain=0.5),
        ],
        frames=2,
    )

    assert output == [
        0.25, 0.25, 0.0, 0.0, 0.25, 0.0, 0.0, 0.0,
        0.5, 0.5, 0.0, 0.0, -0.25, 0.0, 0.0, 0.0,
    ]


def test_mixer_limits_summed_output() -> None:
    mixer = MultichannelMixer(output_channels=2, limiter=0.8)

    output = mixer.mix(
        [
            MixInput(samples=(0.75,), output_channels=(0,)),
            MixInput(samples=(0.75,), output_channels=(0,)),
        ],
        frames=1,
    )

    assert output == [0.8, 0.0]


def test_mixer_rejects_bad_frame_shape() -> None:
    mixer = MultichannelMixer()

    with pytest.raises(ValueError, match="exactly one mono sample"):
        mixer.mix([MixInput(samples=(0.1,), output_channels=(0,))], frames=2)
