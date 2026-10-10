"""Central paths for source runs and portable Windows builds."""
from __future__ import annotations

import os
import sys
from pathlib import Path


def app_root() -> Path:
    """Writable folder beside the executable, or the repository in source runs."""
    override = os.environ.get("MARK_LV_HOME")
    if override:
        return Path(override).expanduser().resolve()
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


def resource_root() -> Path:
    """Bundled read-only resources, or the repository in source runs."""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", app_root())).resolve()
    return app_root()


def data_path(*parts: str) -> Path:
    """Return a writable path kept under the portable application folder."""
    return app_root().joinpath(*parts)
