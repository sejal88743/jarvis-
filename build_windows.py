"""Build a portable Windows one-folder distribution for MARK LV."""
from __future__ import annotations

import shutil
import subprocess
import sys
import argparse
from pathlib import Path

from core.paths import app_root


ROOT = app_root()
APP_NAME = "MARK-LV"
BUILD_DIR = ROOT / "build" / "pyinstaller"
DIST_DIR = ROOT / "dist"
APP_DIR = DIST_DIR / APP_NAME


def _dynamic_modules(folder: Path) -> list[str]:
    return [
        f"{folder.name}.{path.stem}"
        for path in sorted(folder.glob("*.py"))
        if path.name != "__init__.py" and not path.name.startswith("_")
    ]


def package_portable() -> None:
    if not (APP_DIR / (APP_NAME + ".exe")).exists():
        raise SystemExit(f"Build executable not found: {APP_DIR / (APP_NAME + '.exe')}")
    for folder in ("actions", "plugins"):
        shutil.copytree(
            ROOT / folder,
            APP_DIR / folder,
            dirs_exist_ok=True,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )
    for folder in ("config", "memory", "models", "core"):
        (APP_DIR / folder).mkdir(parents=True, exist_ok=True)
    shutil.copy2(ROOT / "config" / "jarvis.ico", APP_DIR / "config" / "jarvis.ico")
    shutil.copy2(ROOT / "core" / "face_model.obj", APP_DIR / "core" / "face_model.obj")

    readme = APP_DIR / "PORTABLE-SETUP.txt"
    readme.write_text(
        "MARK LV portable folder\n\n"
        "Run MARK-LV.exe. On first launch, enter your Gemini API key.\n"
        "Keep config, memory, actions, plugins and _internal beside the EXE.\n"
        "Your configuration and memory are stored in this folder.\n"
        "Add/remove plugins in the plugins folder, then restart MARK LV.\n",
        encoding="utf-8",
    )
    print(f"Portable build ready: {APP_DIR / (APP_NAME + '.exe')}")


def build() -> None:
    if sys.platform != "win32":
        raise SystemExit("Build this portable executable on Windows.")
    if sys.version_info < (3, 10):
        raise SystemExit("MARK LV requires Python 3.10 or newer to build.")
    if not (ROOT / "config" / "jarvis.ico").exists():
        raise SystemExit("Missing app icon: config/jarvis.ico")

    dynamic_imports = _dynamic_modules(ROOT / "actions") + _dynamic_modules(ROOT / "plugins")
    command = [
        sys.executable, "-m", "PyInstaller", "--noconfirm", "--onedir",
        "--windowed", "--name", APP_NAME, "--contents-directory", "_internal",
        "--distpath", str(DIST_DIR), "--workpath", str(BUILD_DIR),
        "--specpath", str(BUILD_DIR), "--icon", str(ROOT / "config" / "jarvis.ico"),
        "--add-data", f"{ROOT / 'core' / 'prompt.txt'};core",
        "--add-data", f"{ROOT / 'core' / 'face_model.obj'};core",
        "--add-data", f"{ROOT / 'dashboard' / 'static'};dashboard/static",
        "--collect-data", "openwakeword",
        "--collect-binaries", "onnxruntime",
        "--exclude-module", "onnxruntime.tools",
        "--exclude-module", "onnxruntime.quantization",
        "--exclude-module", "onnxruntime.transformers",
        "--exclude-module", "sklearn",
        "--hidden-import", "actions",
        "--hidden-import", "plugins",
    ]
    for module in dynamic_imports:
        command.extend(("--hidden-import", module))
    command.append(str(ROOT / "main.py"))

    subprocess.run(command, cwd=ROOT, check=True)
    package_portable()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--package-only", action="store_true")
    args = parser.parse_args()
    package_portable() if args.package_only else build()