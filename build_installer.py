"""
build_installer.py — ONE command that produces a one-click Windows installer.

WHAT IT DOES
    python build_installer.py

        1. checks the build prerequisites (Windows, right Python, PyInstaller)
        2. installs the build-time dependencies if they are missing
        3. runs the PyInstaller build (build_windows.py)
        4. drops the install marker so the installed copy writes its data to
           AppData instead of Program Files
        5. compiles the Inno Setup script into a single setup .exe

    The result:

        dist/installer/MARK-LV-<version>-Setup.exe

    One file. The user double-clicks it, picks a folder, and gets Start Menu
    and desktop shortcuts, an entry in Add/Remove Programs, and an uninstaller.
    Their API key and memory are never touched by installing or uninstalling.

WHY THE MARKER FILE
    An installed copy under Program Files cannot write beside its own .exe — so
    core/paths.py sends its writable state to %APPDATA%\\MARK-LV. A PORTABLE
    copy must keep everything in its own folder. Nothing about the path can tell
    the two apart (a user may install to D:\\ too), so the installer writes
    `installed.marker` next to the executable and the app reads that.

OPTIONS
    --skip-build      reuse the existing dist/MARK-LV folder
    --inno PATH       path to ISCC.exe if it is not in the default location
    --version X.Y.Z   version stamped into the installer and the EXE metadata
    --portable-only   skip the installer and just (re)package the portable folder
"""
from __future__ import annotations

import argparse
import platform
import re
import shutil
import subprocess
import sys
from pathlib import Path

# ── The console must survive a non-UTF-8 code page ───────────────────────────
# Every status line here carries an emoji or an arrow, and on a legacy Windows
# console the active code page is the system one — cp1254 in Turkey, cp1251 in
# Russia, cp932 in Japan. Printing one raises UnicodeEncodeError and takes the
# build down at the very end, after the expensive steps have already run. The
# app itself reconfigures for exactly this reason; so does the builder.
for _stream in ("stdout", "stderr"):
    try:
        _s = getattr(sys, _stream, None)
        if _s is not None and hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

ROOT = Path(__file__).resolve().parent
APP_NAME = "MARK-LV"
DIST = ROOT / "dist" / APP_NAME
INSTALLER_OUT = ROOT / "dist" / "installer"
ICON = ROOT / "config" / "jarvis.ico"

# Where Inno Setup 6 lands by default on a normal Windows machine.
_INNO_CANDIDATES = (
    Path(r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe"),
    Path(r"C:\Program Files\Inno Setup 6\ISCC.exe"),
    Path.home() / "AppData" / "Local" / "Programs" / "Inno Setup 6" / "ISCC.exe",
)


def _say(msg: str) -> None:
    print(f"  {msg}")


def _step(n: int, total: int, title: str) -> None:
    print(f"\n[{n}/{total}] {title}")


def _run(args: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(args, cwd=ROOT, **kw)


# ── version ──────────────────────────────────────────────────────────────────

def detect_version() -> str:
    """Pull a version out of the readme if it names one, else date-stamp.

    The readme is the only place a version is written down by hand, so using it
    keeps the installer, the EXE metadata and the documentation in step. If it
    cannot be found, a date is honest and never wrong.
    """
    try:
        text = (ROOT / "readme.md").read_text(encoding="utf-8", errors="ignore")
        m = re.search(r"\b(?:version|release|v)\s*([0-9]+\.[0-9]+\.[0-9]+)\b",
                      text, re.IGNORECASE)
        if m:
            return m.group(1)
    except Exception:
        pass
    from datetime import date
    return f"1.0.{date.today().strftime('%y%m%d')}"


# ── prerequisites ────────────────────────────────────────────────────────────

def check_prerequisites() -> None:
    if sys.platform != "win32":
        raise SystemExit(
            "The one-click installer is built on Windows.\n"
            "On macOS or Linux, run `python main.py` from a checkout, or ship the\n"
            "portable folder — the .exe installer is a Windows artefact."
        )
    if sys.version_info < (3, 10):
        raise SystemExit(
            f"Python {sys.version_info.major}.{sys.version_info.minor} is too old. "
            f"MARK LV builds on 3.10 or newer."
        )
    if not ICON.exists():
        raise SystemExit(
            f"Missing app icon: {ICON}\n"
            "The installer and the shortcuts need it."
        )


def find_iscc(explicit: str = "") -> Path | None:
    """Locate the Inno Setup compiler, or explain how to get it."""
    if explicit:
        p = Path(explicit)
        return p if p.exists() else None
    found = shutil.which("ISCC") or shutil.which("iscc")
    if found:
        return Path(found)
    for candidate in _INNO_CANDIDATES:
        if candidate.exists():
            return candidate
    return None


def ensure_pyinstaller() -> None:
    try:
        import PyInstaller  # noqa: F401
        return
    except ImportError:
        pass
    _say("PyInstaller is missing — installing it now (one time, ~10 MB)…")
    proc = _run([sys.executable, "-m", "pip", "install",
                 "-r", "requirements-build.txt"])
    if proc.returncode != 0:
        raise SystemExit("Could not install the build dependencies.")


# ── steps ────────────────────────────────────────────────────────────────────

def write_install_marker() -> None:
    """Mark this copy as installed, so core/paths.py keeps user data in AppData.

    Also makes sure the folders the app expects exist, so a first launch has
    somewhere to write.
    """
    if not DIST.exists():
        raise SystemExit(f"Build folder not found: {DIST}")

    marker = DIST / "installed.marker"
    marker.write_text(
        "This file marks an INSTALLED copy of MARK LV.\n"
        "\n"
        "core/paths.py reads it and keeps the user's writable data in\n"
        "%APPDATA%\\MARK-LV instead of beside the executable, because Program\n"
        "Files is read-only and an upgrade there would wipe an API key.\n"
        "\n"
        "Delete this file to turn the folder back into a portable copy — the\n"
        "app will then keep its config and memory beside the executable.\n",
        encoding="utf-8",
    )
    for folder in ("config", "memory", "skills", "actions", "plugins"):
        (DIST / folder).mkdir(parents=True, exist_ok=True)

    # A portable copy needs a plugins folder to exist; an installed one reads
    # the user's from AppData, but shipping an empty one harms nothing and keeps
    # the folder self-explanatory.
    _say("install marker written (user data will go to %APPDATA%\\MARK-LV)")


def copy_runtime_skills() -> None:
    """Make sure the build folder carries the skills/ tree.

    build_windows.py packages actions/ and plugins/; skills/ is the newer folder
    and must ship too, or a fresh install launches with none of the new skills.
    """
    src = ROOT / "skills"
    if not src.exists():
        return
    dst = DIST / "skills"
    dst.mkdir(parents=True, exist_ok=True)
    for path in src.glob("*.py"):
        if path.name == "__init__.py":
            continue
        shutil.copy2(path, dst / path.name)
    _say(f"skills packaged: {len(list(dst.glob('*.py')))} file(s)")


def run_pyinstaller() -> None:
    _say("running PyInstaller (this takes a few minutes the first time)…")
    proc = _run([sys.executable, "build_windows.py"])
    if proc.returncode != 0:
        raise SystemExit(
            "The PyInstaller build failed.\n"
            "Scroll up for the first error — it is usually a missing package.\n"
            "Then retry with:  python build_installer.py --skip-build"
        )


def compile_installer(iscc: Path, version: str) -> Path:
    INSTALLER_OUT.mkdir(parents=True, exist_ok=True)
    iss = ROOT / "installer" / "MARK-LV.iss"
    if not iss.exists():
        raise SystemExit(f"Installer script missing: {iss}")

    _say("compiling the installer…")
    proc = _run([
        str(iscc),
        f"/DAppVersion={version}",
        str(iss),
    ], capture_output=True, text=True, encoding="utf-8", errors="replace")

    if proc.returncode != 0:
        print(proc.stdout or "")
        print(proc.stderr or "")
        raise SystemExit("Inno Setup reported an error (above).")

    expected = INSTALLER_OUT / f"{APP_NAME}-{version}-Setup.exe"
    if expected.exists():
        return expected
    # Inno names the file from the script; find whatever it produced.
    produced = sorted(INSTALLER_OUT.glob("*Setup.exe"), key=lambda p: p.stat().st_mtime)
    if produced:
        return produced[-1]
    raise SystemExit("The installer compiled but no .exe was found in dist/installer.")


# ── main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build the one-click MARK LV Windows installer.")
    parser.add_argument("--skip-build", action="store_true",
                        help="reuse dist/MARK-LV instead of re-running PyInstaller")
    parser.add_argument("--inno", default="",
                        help="full path to ISCC.exe if it is not found automatically")
    parser.add_argument("--version", default="",
                        help="version stamped into the installer (e.g. 55.0.1)")
    parser.add_argument("--portable-only", action="store_true",
                        help="package the portable folder and stop — no installer")
    args = parser.parse_args()

    total = 3 if args.portable_only else 6
    version = args.version or detect_version()

    print(f"MARK LV — one-click installer builder")
    print(f"  version : {version}")
    print(f"  python  : {sys.version.split()[0]}")
    print(f"  root    : {ROOT}")

    _step(1, total, "Checking prerequisites")
    check_prerequisites()
    _say(f"Windows {platform.release()}, Python {sys.version_info.major}."
         f"{sys.version_info.minor} — OK")

    iscc = None
    if not args.portable_only:
        _step(2, total, "Checking the build toolchain")
        ensure_pyinstaller()
        iscc = find_iscc(args.inno)
        if iscc is None:
            raise SystemExit(
                "\nInno Setup 6 was not found — it is what produces the setup .exe.\n"
                "\n"
                "  1. Download it (free):  https://jrsoftware.org/isdl.php\n"
                "  2. Install it with the default options\n"
                "  3. Run this script again\n"
                "\n"
                "If you put it somewhere unusual, point at it directly:\n"
                "    python build_installer.py --inno \"D:\\Tools\\Inno\\ISCC.exe\"\n"
                "\n"
                "Or run with --portable-only to skip the installer and just\n"
                "produce the portable folder.\n"
            )
        _say(f"toolchain ready (Inno at {iscc})")

    if not args.skip_build:
        _step(3 if not args.portable_only else 2, total,
              "Building the application (PyInstaller)")
        run_pyinstaller()
    else:
        _step(3 if not args.portable_only else 2, total, "Reusing the existing build")
        if not DIST.exists():
            raise SystemExit(f"--skip-build was given but {DIST} does not exist.")

    _step(4 if not args.portable_only else 3, total, "Packaging runtime files")
    write_install_marker()
    copy_runtime_skills()

    if args.portable_only:
        print(f"\n✅ Portable build ready:\n   {DIST}\\{APP_NAME}.exe")
        print("   (No install marker semantics changed — this folder is portable.)")
        # A portable copy must NOT be marked as installed.
        (DIST / "installed.marker").unlink(missing_ok=True)
        print("   installed.marker removed — config and memory stay in this folder.")
        return

    _step(5, total, "Compiling the one-click installer")
    setup_exe = compile_installer(iscc, version)

    _step(6, total, "Done")

    size_mb = setup_exe.stat().st_size / (1024 * 1024)
    print(f"""
✅ One-click installer ready:

   {setup_exe}
   {size_mb:.1f} MB

   Give that single file to anyone. Double-clicking it installs MARK LV with
   Start Menu and optional desktop shortcuts, adds it to Add/Remove Programs,
   and offers to launch it when it finishes.

   Their API key and everything the assistant remembers live in:
       %APPDATA%\\MARK-LV
   so upgrading or uninstalling never touches them.

   Portable folder (no installer, runs from anywhere):
       {DIST}\\{APP_NAME}.exe
""")


if __name__ == "__main__":
    main()
