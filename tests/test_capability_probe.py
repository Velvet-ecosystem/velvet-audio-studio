from velvet_audio_studio.adapters.alsa.capability_probe import parse_hw_params


SAMPLE = """
ACCESS:  MMAP_INTERLEAVED RW_INTERLEAVED
FORMAT:  S16_LE S24_LE S32_LE
SUBFORMAT:  STD
SAMPLE_BITS: [16 32]
FRAME_BITS: [128 256]
CHANNELS: [2 8]
RATE: [44100 96000]
"""


def test_parse_hw_params_extracts_channel_rate_and_format_ranges() -> None:
    capabilities = parse_hw_params(SAMPLE, device="hw:CARD=audioinjectoroc", direction="playback")

    assert capabilities.available is True
    assert capabilities.channels_min == 2
    assert capabilities.channels_max == 8
    assert capabilities.rates == (44100, 96000)
    assert capabilities.formats == ("S16_LE", "S24_LE", "S32_LE")


def test_parse_hw_params_reports_missing_fields() -> None:
    capabilities = parse_hw_params("ACCESS: RW_INTERLEAVED", device="hw:1", direction="capture")

    assert capabilities.available is False
    assert "channel range not reported" in capabilities.degraded_reasons
    assert "sample formats not reported" in capabilities.degraded_reasons
    assert "sample rates not reported" in capabilities.degraded_reasons
