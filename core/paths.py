"""Central paths for source runs, portable builds and installed copies."""
from __future__ import annotations

import os
import sys
from pathlib import Path


# Set by the installer to mark an installed (as opposed to portable) copy. The
# name is deliberately specific so it cannot collide with anything else.
_INSTALL_MARKER = "installed.marker"


def _install_path() -> Path:
    """Where an INSTALLED copy keeps the user's data.

    Why this exists
    ---------------
    When MARK LV is installed under Program Files, the folder beside the .exe is
    read-only for a normal user — writing api_keys.json or long_term.json there
    fails with an access-denied the user cannot act on. And it is the wrong
    place anyway: an upgrade or a reinstall replaces Program Files, which would
    wipe their API key and everything the assistant remembers.

    So an installed copy keeps its writable state in the per-user AppData
    folder, which survives upgrades and needs no elevation. A PORTABLE copy
    keeps the old behaviour — everything beside the .exe — which is what makes
    it portable.

    Which one this is, is decided by a marker file the installer drops next to
    the executable, not by guessing from the path (a user may legitimately
    install to D:\\MARK-LV or run a portable copy from Program Files).
    """
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or (Path.home() / "AppData" / "Roaming")
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = os.environ.get("XDG_DATA_HOME") or (Path.home() / ".local" / "share")
    return Path(base) / "MARK-LV"


def _is_installed(exe_dir: Path) -> bool:
    """True when the installer marked this copy as installed."""
    try:
        return (exe_dir / _INSTALL_MARKER).exists()
    except Exception:
        return False


def app_root() -> Path:
    """The folder holding this user's writable data.

    Order of precedence:
      1. MARK_LV_HOME, for tests and for anyone who wants to pin it.
      2. Per-user AppData, when the installer marked this copy as installed.
      3. Beside the executable (portable build) or the repository (source run).
    """
    override = os.environ.get("MARK_LV_HOME")
    if override:
        return Path(override).expanduser().resolve()
    if getattr(sys, "frozen", False):
        exe_dir = Path(sys.executable).resolve().parent
        if _is_installed(exe_dir):
            path = _install_path()
            try:
                path.mkdir(parents=True, exist_ok=True)
            except Exception:
                # If AppData is somehow unwritable, falling back to the exe
                # folder is worse but at least keeps the app running.
                return exe_dir
            return path
        return exe_dir
    return Path(__file__).resolve().parents[1]


def resource_root() -> Path:
    """Bundled read-only resources, or the repository in source runs."""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent)).resolve()
    return Path(__file__).resolve().parents[1]


def install_root() -> Path:
    """Where the program itself lives — beside the executable when frozen, or the
    repository in a source run. Distinct from app_root(), which is where the
    USER's data goes; a frozen installed copy has the two in different places."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


def data_path(*parts: str) -> Path:
    """Return a writable path kept under this user's application folder."""
    return app_root().joinpath(*parts)
