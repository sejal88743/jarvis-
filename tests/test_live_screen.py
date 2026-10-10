import asyncio

import main


class FakeLiveSession:
    def __init__(self):
        self.video = None

    async def send_realtime_input(self, *, video):
        self.video = video


def test_live_screen_frame_uses_live_video_input(monkeypatch):
    monkeypatch.setattr(main, "_capture_screen", lambda: (b"jpeg-frame", "image/jpeg"))
    session = FakeLiveSession()

    asyncio.run(main._send_live_screen_frame(session))

    assert session.video.data == b"jpeg-frame"
    assert session.video.mime_type == "image/jpeg"


def test_live_screen_prompt_explains_streaming_mode():
    limits = main._describe_limits(has_vision=True, has_mic=True, live_screen=True)

    assert "desktop frames are sent" in limits
    assert "one frame per second" in limits
