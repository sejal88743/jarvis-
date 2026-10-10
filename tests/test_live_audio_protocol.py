import main


def test_live_pcm_mime_type_declares_sample_rate():
    assert main._pcm_mime_type(main.SEND_SAMPLE_RATE) == "audio/pcm;rate=16000"


def test_live_input_and_output_rates_are_positive():
    assert main.SEND_SAMPLE_RATE > 0
    assert main.RECEIVE_SAMPLE_RATE > 0