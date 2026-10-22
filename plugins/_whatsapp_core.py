"""
_whatsapp_core.py — the verified WhatsApp driver for desktop WhatsApp.

Why this exists
    actions/send_message.py tries to import this module first. Without it,
    every WhatsApp send degraded to "typing blind": Win key, type the app
    name, Ctrl+F, type the contact, Enter twice — with nothing ever reading
    back which window received the keystrokes. A slow machine could land the
    whole sequence in the wrong conversation and the user would still be told
    the message went.

What it does
    get()          — returns a transport that drives the real WhatsApp window.
    send_message_to(receiver, message) — opens the conversation, verifies the
                     header actually shows the receiver, types, sends, and
                     CONFIRMS the input box emptied before reporting success.
    read_last_messages(receiver, n)    — opens the conversation and reads the
                     last n message bubbles off the screen with Gemini.

Everything drives the visible WhatsApp Desktop window with pyautogui, and
every step is verified with a screenshot. If a step cannot be verified the
driver refuses rather than guessing — that is the whole point.
"""
from __future__ import annotations

import io
import re
import time
from pathlib import Path

try:
    import pyautogui
    pyautogui.FAILSAFE = True
    _PYAUTOGUI = True
except ImportError:
    _PYAUTOGUI = False

try:
    import pyperclip
    _PYPERCLIP = True
except ImportError:
    _PYPERCLIP = False

_BASE = Path(__file__).resolve().parent.parent


def _cfg() -> dict:
    try:
        import json
        return json.loads((_BASE / "config" / "api_keys.json")
                          .read_text(encoding="utf-8"))
    except Exception:
        return {}


def _os() -> str:
    return (_cfg().get("os_system") or "windows").lower()


def _require_gui() -> None:
    if not _PYAUTOGUI:
        raise RuntimeError("pyautogui not installed. Run: pip install pyautogui")


# ── low-level helpers ────────────────────────────────────────────────────────

def _paste(text: str) -> None:
    if _PYPERCLIP:
        pyperclip.copy(text)
        time.sleep(0.15)
        pyautogui.hotkey("ctrl", "v")
    else:
        pyautogui.write(text, interval=0.03)
    time.sleep(0.1)


def _grab() -> tuple[bytes, str]:
    """Screenshot of the whole screen as PNG bytes."""
    import io
    img = pyautogui.screenshot()
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue(), "image/png"


def _vision(prompt: str) -> str:
    """One Gemini call on the current screen; empty string on any failure."""
    try:
        from google.genai import types as gtypes
        from core import gemini
        data, mime = _grab()
        resp = gemini.call(
            [gtypes.Part.from_bytes(data=data, mime_type=mime), prompt],
            tier=gemini.FAST, timeout_ms=20_000,
        )
        if resp is None:
            return ""
        return (resp.text or "").strip()
    except Exception as e:
        print(f"[WhatsAppCore] vision failed: {e}")
        return ""


def _screen_contains(description: str) -> tuple[int, int] | None:
    """Find a UI element on screen; (x, y) or None."""
    text = _vision(
        f"This is a screenshot of the computer screen. Locate the UI element "
        f"described as: '{description}'. Reply ONLY with center coordinates "
        f"as: x,y  If it is not visible, reply: NOT_FOUND"
    )
    if not text or "NOT_FOUND" in text.upper():
        return None
    m = re.search(r"(\d+)\s*,\s*(\d+)", text)
    return (int(m.group(1)), int(m.group(2))) if m else None


def _window_on_screen() -> bool:
    """Is the WhatsApp Desktop window up front?"""
    text = _vision(
        "Is a WhatsApp Desktop application window visible and focused in this "
        "screenshot (the chat list on the left, a conversation pane on the "
        "right)? Reply exactly YES or NO."
    )
    return "YES" in text.upper()


def _open_app() -> bool:
    from actions.send_message import _open_app as _launch
    if not _launch("WhatsApp"):
        return False
    time.sleep(2.0)
    # If the window was already open but behind something, focus it.
    if not _window_on_screen():
        try:
            from actions.computer_control import _focus_window
            _focus_window("WhatsApp")
            time.sleep(1.0)
        except Exception:
            pass
    return _window_on_screen()


# ── the transport ────────────────────────────────────────────────────────────

class _Transport:
    """Drives the real WhatsApp window with verification at every step."""

    def open_conversation(self, receiver: str) -> bool:
        """Open the chat with `receiver` and verify the header shows them."""
        _require_gui()
        # Ctrl+F opens the search field in WhatsApp Desktop.
        pyautogui.hotkey("ctrl", "f")
        time.sleep(0.6)
        _paste(receiver)
        time.sleep(1.8)
        pyautogui.press("enter")
        time.sleep(1.5)
        # Verify: the conversation header should now carry the receiver's name.
        text = _vision(
            f"This is a WhatsApp Desktop conversation view. Does the chat "
            f"header at the top of the conversation pane show the contact "
            f"'{receiver}' (or a name containing it)? Reply exactly YES or NO."
        )
        return "YES" in text.upper()

    def send_message_to(self, receiver: str, message: str) -> tuple[bool, str]:
        if not _open_app():
            return False, "the WhatsApp window could not be opened or focused"

        if not self.open_conversation(receiver):
            return False, (f"the conversation with '{receiver}' could not be "
                           f"opened, or the header did not confirm it")

        # Type into the message box, then confirm what we typed is in it.
        pyautogui.click(600, 900)   # the input box; a miss is harmless
        text = _vision(
            "Where is the message input box of this WhatsApp conversation "
            "(the box at the bottom where you type before pressing Enter)? "
            "Reply ONLY with center coordinates as: x,y"
        )
        m = re.search(r"(\d+)\s*,\s*(\d+)", text or "")
        if m:
            pyautogui.click(int(m.group(1)), int(m.group(2)))
        time.sleep(0.3)

        _paste(message)
        time.sleep(0.4)

        # Verify the box actually holds our message before pressing Enter.
        check = _vision(
            "This is a WhatsApp conversation. Is the message input box at the "
            "bottom filled with typed text right now? Reply exactly YES or NO."
        )
        if "YES" not in check.upper():
            return False, "the typed message did not land in the input box"

        pyautogui.press("enter")
        time.sleep(0.8)

        # Verify the box emptied — that is what "sent" means here.
        after = _vision(
            "This is a WhatsApp conversation. Is the message input box at the "
            "bottom EMPTY now (a message was just sent)? Reply exactly YES or NO."
        )
        if "YES" not in after.upper():
            return False, "the input box did not empty after Enter — not sent"

        return True, ""

    def read_last_messages(self, receiver: str, count: int = 10) -> list[str]:
        """Open the conversation and read the last `count` messages."""
        _require_gui()
        if not _open_app():
            return []
        if not self.open_conversation(receiver):
            return []
        img, mime = _grab()
        from google.genai import types as gtypes
        from core import gemini
        resp = gemini.call(
            [gtypes.Part.from_bytes(data=img, mime_type=mime),
             f"Read the last {count} messages in this WhatsApp conversation, "
             f"oldest first. For each give 'sender' (them/you) and the text. "
             f"Return STRICT JSON: "
             f'{{"messages": [{{"sender": "them|you", "text": "..."}}]}}. '
             f"JSON only."],
            tier=gemini.SMART, timeout_ms=30_000,
        )
        if resp is None:
            return []
        import json
        t = (resp.text or "").strip()
        start, end = t.find("{"), t.rfind("}")
        if start < 0 or end <= start:
            return []
        try:
            data = json.loads(t[start:end + 1])
            return list(data.get("messages") or [])
        except Exception:
            return []


def get() -> tuple["_Transport | None", str]:
    """Return (transport, why) — why explains a None."""
    if not _PYAUTOGUI:
        return None, "pyautogui is not installed"
    if _os() not in ("windows", "mac", "linux"):
        return None, f"unsupported OS: {_os()}"
    return _Transport(), ""
