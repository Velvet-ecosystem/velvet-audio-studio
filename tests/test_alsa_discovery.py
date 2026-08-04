from velvet_audio_studio.adapters.alsa.card_discovery import find_card, parse_proc_asound_cards


def test_parse_and_find_octo_card() -> None:
    sample = """
 0 [vc4hdmi0       ]: vc4-hdmi - vc4-hdmi-0
 1 [audioinjectoroc]: audioinjector-octo-soundcard - audioinjector-octo-soundcard
"""
    cards = parse_proc_asound_cards(sample)
    assert len(cards) == 2
    octo = find_card(cards)
    assert octo is not None
    assert octo.index == 1
    assert octo.hw_name == "hw:1"


def test_missing_octo_returns_none() -> None:
    cards = parse_proc_asound_cards("0 [Headphones]: bcm2835 Headphones")
    assert find_card(cards) is None
