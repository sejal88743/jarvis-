"""
core/deps.py — ONE dependency manager for the whole application.

THE PROBLEM THIS SOLVES
    Dependency knowledge was scattered across four places that did not know
    about each other:

        requirements.txt          what setup.py installs
        core/installer.py         a second hardcoded list, for the STT/TTS engines
        actions/*.py              `import x` inside try/except, at point of use
        plugins/*.py              same, plus a note telling the user to pip install

    So "is this feature available?" had four different answers, and a missing
    package surfaced as a silent no-op in one place and a pip instruction in
    another. Nothing could answer the one question that matters: *what does this
    machine still need?*

WHAT THIS IS
    A single manifest — MODULES — mapping an import name to how it is obtained.
    On top of it: a cached check, a one-click repair, and a report the assistant
    can read out. Every skill can declare `requires=["requests>=2.31"]` and this
    module decides whether it is present, installs it if the user asks, and
    tells the model honestly when it is not.

DESIGN
    * Checking is `importlib.util.find_spec`, so nothing is imported to answer
      "is it here?" — no side effects, no slow imports at startup.
    * The manifest is DATA, so adding a dependency is one tuple, not a new code
      path in four files.
    * Installation reports through a callback and never raises; a failed install
      degrades exactly one skill.
"""
from __future__ import annotations

import importlib.util
import platform
import subprocess
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

_OS = platform.system()
_IS_WIN = _OS == "Windows"


@dataclass(frozen=True)
class Dep:
    """One installable dependency: how to import it, and how to get it."""
    module: str            # import name, e.g. "PIL"
    package: str           # pip name,   e.g. "pillow"
    why: str = ""          # what breaks without it, in the user's terms
    optional: bool = False # not needed for the app to run at all
    systems: tuple[str, ...] = ()   # () = every OS


# ── THE MANIFEST ─────────────────────────────────────────────────────────────
# Everything the app or any bundled skill can need, in one list. Grouped for
# readability; the code below treats it as one flat table.

MANIFEST: tuple[Dep, ...] = (
    # ── Core: UI, audio, model ───────────────────────────────────────────────
    Dep("PyQt6",             "PyQt6>=6.6,<7",                 "the interface"),
    Dep("sounddevice",       "sounddevice>=0.4,<1",           "microphone and speakers"),
    Dep("numpy",             "numpy>=1.24,<3",                "audio processing and the avatar"),
    Dep("google.genai",      "google-genai>=2.8.0,<3",        "the AI itself"),

    # ── Vision & media ───────────────────────────────────────────────────────
    Dep("PIL",               "pillow>=10,<13",                "images and screenshots"),
    Dep("cv2",               "opencv-python>=4.8,<5",         "camera capture"),
    Dep("mss",               "mss>=9,<11",                    "screen capture"),
    Dep("yt_dlp",            "yt-dlp",                        "YouTube on the HUD", optional=True),

    # ── Automation ───────────────────────────────────────────────────────────
    Dep("pyautogui",         "pyautogui",                     "mouse and keyboard control"),
    Dep("pyperclip",         "pyperclip",                     "clipboard read and write"),
    Dep("playwright",        "playwright>=1.40,<2",           "browser automation"),
    Dep("pygetwindow",       "pygetwindow",                   "window geometry", optional=True),

    # ── Web ──────────────────────────────────────────────────────────────────
    Dep("requests",          "requests>=2.31,<3",             "web requests"),
    Dep("bs4",               "beautifulsoup4>=4.12,<5",       "reading web pages"),
    Dep("ddgs",              "ddgs>=9,<10",                   "web search", optional=True),

    # ── System, files & documents ────────────────────────────────────────────
    Dep("psutil",            "psutil>=5.9,<8",                "system monitoring"),
    Dep("send2trash",        "send2trash",                    "recoverable deletes", optional=True),
    Dep("pptx",              "python-pptx",                   "reading presentations", optional=True),
    Dep("pdfplumber",        "pdfplumber",                    "reading PDFs", optional=True),
    Dep("PyPDF2",            "PyPDF2",                        "reading PDFs (fallback)", optional=True),
    Dep("docx",              "python-docx",                   "reading Word documents", optional=True),
    Dep("openpyxl",          "openpyxl",                      "spreadsheet output", optional=True),
    Dep("pynvml",            "nvidia-ml-py>=12,<13",          "NVIDIA GPU readings", optional=True),

    # ── Remote dashboard ─────────────────────────────────────────────────────
    Dep("fastapi",           "fastapi>=0.110,<1",             "the phone dashboard", optional=True),
    Dep("uvicorn",           "uvicorn[standard]>=0.27,<1",    "the phone dashboard", optional=True),
    Dep("cryptography",      "cryptography>=42,<50",          "the dashboard's TLS", optional=True),
    Dep("multipart",         "python-multipart>=0.0.9,<1",    "the dashboard's uploads", optional=True),
    Dep("qrcode",            "qrcode[pil]>=7,<9",             "the dashboard's QR code", optional=True),

    # ── Skill extras ─────────────────────────────────────────────────────────
    Dep("googleapiclient",   "google-api-python-client",      "Gmail and Calendar skills", optional=True),
    Dep("google_auth_oauthlib", "google-auth-oauthlib",       "Gmail and Calendar sign-in", optional=True),
    Dep("tinytuya",          "tinytuya",                      "smart bulbs", optional=True),
    Dep("paho",              "paho-mqtt",                     "MQTT printers", optional=True),
    Dep("youtube_transcript_api", "youtube-transcript-api",   "YouTube transcripts", optional=True),

    # ── Windows only ─────────────────────────────────────────────────────────
    Dep("comtypes",          "comtypes",     "Windows system control", systems=("Windows",)),
    Dep("pycaw",             "pycaw",        "Windows volume",         systems=("Windows",)),
    Dep("win10toast",        "win10toast",   "Windows notifications",  systems=("Windows",), optional=True),
    Dep("win32com",          "pywin32",      "Windows shortcuts and the Recycle Bin", systems=("Windows",)),
    Dep("wmi",               "wmi",          "Windows hardware data",  systems=("Windows",), optional=True),
    Dep("pywinauto",         "pywinauto",    "Windows accessibility driving", systems=("Windows",), optional=True),

    # ── Optional heavy extras (not installed unless asked for) ───────────────
    Dep("pandas",            "pandas",       "spreadsheet and CSV analysis", optional=True),
    Dep("pydub",             "pydub",        "audio metadata and conversion", optional=True),
    Dep("openwakeword",      "openwakeword", "'Hey Jarvis' wake word",       optional=True),
)

_BY_MODULE: dict[str, Dep] = {d.module: d for d in MANIFEST}

# Normalise a few import names that differ from their top-level package, so a
# skill can write the name it actually imports in its code.
_ALIASES = {
    "google.genai": "google.genai",
    "genai": "google.genai",
    "PIL.Image": "PIL",
    "cv2": "cv2",
    "googleapiclient.discovery": "googleapiclient",
    "sklearn": "scikit-learn",
}


def _canonical(module: str) -> str:
    return _ALIASES.get(module, module)


_lock = threading.Lock()
_checked: dict[str, bool] = {}


def available(module: str) -> bool:
    """Is `module` importable? Cached, and answers without importing anything.

    A skill that guards its import with try/except can call this first to decide
    whether to bother, and the answer is cheap enough to ask on every call.
    """
    key = _canonical(str(module or "").strip())
    if not key:
        return False
    with _lock:
        if key in _checked:
            return _checked[key]
    try:
        found = importlib.util.find_spec(key) is not None
    except (ImportError, ValueError, ModuleNotFoundError):
        # A dotted name whose parent is missing raises rather than returning None.
        found = False
    with _lock:
        _checked[key] = found
    return found


def pip_name(spec: str) -> str:
    """'requests>=2.31' -> 'requests'. The import name for a pip spec."""
    return spec.strip().split("[")[0].split("=")[0].split(">")[0] \
               .split("<")[0].split("!")[0].strip()


def missing_for(requires: Iterable[str]) -> list[str]:
    """Of a list of pip specs, which ones are not installed on this machine?"""
    missing = []
    for spec in requires or ():
        spec = str(spec or "").strip()
        if not spec:
            continue
        pkg = pip_name(spec)
        dep = _BY_MODULE.get(pkg)
        module = dep.module if dep else pkg
        if not available(module):
            missing.append(spec)
    return missing


def report(include_optional: bool = True) -> str:
    """A plain-language dependency report the assistant can read out."""
    present, absent = [], []
    for dep in MANIFEST:
        if dep.systems and _OS not in dep.systems:
            continue
        if not include_optional and dep.optional:
            continue
        (present if available(dep.module) else absent).append(dep)

    lines = [f"{len(present)} dependency group(s) ready."]
    if absent:
        lines.append(f"{len(absent)} not installed:")
        for d in absent:
            flag = "optional" if d.optional else "REQUIRED"
            why = f" — {d.why}" if d.why else ""
            lines.append(f"  • {d.package} ({flag}){why}")
    return "\n".join(lines)


def install(package: str, log: Callable[[str], None] | None = None,
            timeout: int = 300) -> bool:
    """pip-install one package. Blocking; call it off the UI thread.

    Reports through `log` and never raises — a failed install is a fact to
    surface, not an exception to propagate.
    """
    pkg = str(package or "").strip()
    if not pkg:
        return False

    def _say(msg: str) -> None:
        if log:
            try:
                log(msg)
            except Exception:
                pass

    _say(f"SYS: installing {pkg} …")
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "pip", "install", pkg,
             "--quiet", "--disable-pip-version-check"],
            capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        _say(f"ERR: {pkg} install timed out after {timeout}s.")
        return False
    except Exception as e:
        _say(f"ERR: {pkg} install could not start — {e}")
        return False

    if proc.returncode == 0:
        _say(f"SYS: {pkg} installed.")
        # A package installed after we cached a "missing" answer must not stay
        # cached as missing for the rest of the session.
        with _lock:
            _checked.clear()
        return True
    _say(f"ERR: {pkg} install failed — {(proc.stderr or '').strip()[:160]}")
    return False


def ensure(requires: Iterable[str], log: Callable[[str], None] | None = None,
           auto: bool = False, timeout: int = 300) -> list[str]:
    """Make sure a skill's declared needs are present.

    With auto=False (the default) it only *reports* what is missing — an
    assistant that silently pip-installs packages on a user's machine is not one
    anyone should trust. With auto=True it installs them, which is what the
    explicit "install my dependencies" skill does.

    Returns the list of specs still missing afterwards.
    """
    missing = missing_for(requires)
    if not missing:
        return []
    if not auto:
        return missing
    for spec in missing:
        install(spec, log=log, timeout=timeout)
    return missing_for(requires)


def install_playwright_browser(log: Callable[[str], None] | None = None) -> bool:
    """Download the Chromium build Playwright drives. ~150 MB, one time."""
    def _say(msg: str) -> None:
        if log:
            try:
                log(msg)
            except Exception:
                pass
    try:
        _say("SYS: downloading the Playwright browser (Chromium, ~150 MB) …")
        proc = subprocess.run(
            [sys.executable, "-m", "playwright", "install", "chromium"],
            capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=900,
        )
        ok = proc.returncode == 0
        _say("SYS: browser ready." if ok else
             f"ERR: browser download failed — {(proc.stderr or '').strip()[:160]}")
        return ok
    except Exception as e:
        _say(f"ERR: browser download failed — {e}")
        return False


def refresh() -> None:
    """Forget the cache — call after anything installs a package."""
    with _lock:
        _checked.clear()
