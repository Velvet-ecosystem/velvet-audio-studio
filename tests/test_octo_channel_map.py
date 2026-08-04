import pytest

from velvet_audio_studio.adapters.audio_injector_octo.channel_map import (
    DEFAULT_TIBURON_MAP,
    OctoChannelMap,
)


def test_default_map_uses_all_octo_slots() -> None:
    assert len(DEFAULT_TIBURON_MAP.outputs) == 8
    assert len(DEFAULT_TIBURON_MAP.inputs) == 6
    assert DEFAULT_TIBURON_MAP.output_slot("center_voice") == 4
    assert DEFAULT_TIBURON_MAP.input_slot("center_roof_mic") == 4


def test_wrong_slot_count_is_rejected() -> None:
    with pytest.raises(ValueError):
        OctoChannelMap(outputs=("left", "right"), inputs=("mic",) * 6)
