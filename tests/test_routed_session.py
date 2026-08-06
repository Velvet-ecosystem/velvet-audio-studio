from velvet_audio_studio.studio.routed_session import render_velvet_voice


def test_velvet_voice_routes_to_front_and_center_and_ducks_music() -> None:
    output, receipt = render_velvet_voice(
        (0.5, 0.5),
        background_samples=(0.4, 0.4),
    )

    assert len(output) == 16
    assert receipt.route_id == "velvet.voice.primary"
    assert receipt.output_channels == (0, 1, 4)

    decisions = {decision.source_id: decision for decision in receipt.ducking}
    assert decisions["velvet_tts"].applied_gain == 0.85
    assert decisions["music"].applied_gain == 0.25

    # Frame zero: front pair contain voice plus ducked music; center is voice only.
    assert output[0] == 0.525
    assert output[1] == 0.525
    assert output[4] == 0.425
    assert output[2] == 0.1
    assert output[3] == 0.1


def test_voice_only_session_produces_receipt() -> None:
    output, receipt = render_velvet_voice((0.25,))

    assert receipt.event == "audio.session.started"
    assert output[0] == 0.2125
    assert output[1] == 0.2125
    assert output[4] == 0.2125
