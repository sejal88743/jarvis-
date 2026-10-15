"""
system_access.py — FULL DESKTOP ACCESS for JARVIS ("advanced mode").

WHY THIS EXISTS
    file_controller is deliberately sandboxed to the user's home directory and
    refuses anything outside it. That is the right default, but it also means the
    assistant cannot touch a project on D:\\, a config in ProgramData, or write a
    file next to a running executable. This action is the un-sandboxed twin:

        read / write ANY path           launch ANY app (by name or exact exe)
        run shell commands              inspect drives, processes, environment
        repair its own code             self-test the whole toolchain

WHAT IT IS NOT
    It is not a different assistant. It obeys the same rules: it announces
    destructive work, registers an undo wherever a reversal is possible, and
    reports exactly what it did. What changes is only the reach.

SAFETY MODEL
    - Advanced Mode (`advanced_mode` in config/api_keys.json, default ON) gates
      every action that reaches the filesystem, launches a program or runs a
      command. Turn it off and this action refuses; the rest of the assistant is
      unchanged. Read-only inspection (system_info, drives, processes) is always
      available — it changes nothing and cannot hurt.
    - The one hard rule kept from file_controller: a recursive delete of a drive
      root or of the home directory itself is refused, because nothing can undo
      it and it is never what was meant.
    - Every reversible change pushes onto the shared undo stack (core/undo.py),
      so "undo" in any language still takes it back.

TOOL DISCOVERY
    Auto-discovered by core/action_loader.py from the module-level TOOL dict,
    exactly like every other bundled action.
"""
from __future__ import annotations

import json
import os
import platform
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from core.undo import push_undo

_OS = platform.system()          # "Windows" | "Darwin" | "Linux"
_IS_WIN = _OS == "Windows"

# Which actions reach outside the home folder (or run code). When Advanced Mode
# is off, these refuse — the assistant keeps working exactly as it did before
# this action existed, and routine file_controller calls are unaffected.
_ADVANCED_ONLY = {
    "read", "list", "write", "delete", "mkdir", "move", "copy",
    "search", "grep", "launch", "run", "env", "path_info", "recent",
    "open_with", "clipboard", "zip", "unzip", "cleanup", "kill",
    "checksum", "duplicates",
    "self_locate", "self_test", "self_fix", "self_repair",
}


def _project_root() -> Path:
    """Where this application lives — beside the executable when frozen, or the
    repository root in a source run."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


def _config_path() -> Path:
    """The API-keys config, tolerant of the portable layout."""
    return _project_root() / "config" / "api_keys.json"


def _advanced_enabled() -> bool:
    """Read the Advanced Mode flag, defaulting to ON.

    Read fresh on every call (not cached at import) so the user can flip it in
    config/api_keys.json — or from the UI — and the next command obeys without
    restarting the assistant."""
    try:
        return bool(json.loads(_config_path().read_text(encoding="utf-8"))
                    .get("advanced_mode", True))
    except Exception:
        return True

# Commands that are never worth running from a voice assistant and can take the
# machine down in one line. Not a sandbox — a seatbelt.
_FORBIDDEN_COMMAND_FRAGMENTS = (
    "format ", "diskpart", "mkfs", "rm -rf /", "rm -rf /*",
    "shutdown /s /t 0 && del", "bcdedit", ":(){:|:&};:",
)

# Undo snapshots a file's previous contents; above this the snapshot is skipped
# for the same reason file_controller skips it (a 200 MB log must not sit in RAM).
_UNDO_CONTENT_LIMIT = 1_000_000


# ── helpers ──────────────────────────────────────────────────────────────────

def _expand(raw: str) -> Path:
    """Turn a user/voice-supplied path into a concrete Path, tolerating the
    shortcuts people actually say and Windows back-slashes."""
    raw = (raw or "").strip().strip('"').strip("'")
    shortcuts = {
        "desktop":   Path.home() / "Desktop",
        "downloads": Path.home() / "Downloads",
        "documents": Path.home() / "Documents",
        "pictures":  Path.home() / "Pictures",
        "music":     Path.home() / "Music",
        "videos":    Path.home() / "Videos",
        "home":      Path.home(),
    }
    if raw.lower() in shortcuts:
        return shortcuts[raw.lower()]

    head, sep, rest = raw.replace("\\", "/").partition("/")
    if sep and head.lower() in shortcuts:
        rest = rest.strip("/")
        base = shortcuts[head.lower()]
        return (base / rest) if rest else base

    expanded = os.path.expandvars(os.path.expanduser(raw))
    # `%HOMEPATH%` on Windows expands to a DRIVE-LESS path ("\\Users\\MANOJ"),
    # unlike %USERPROFILE%. The model reaches for %HOMEPATH% because it is the
    # conventional Windows name, so a path it builds lands relative to the
    # current directory and the file reads as "not found". Anchor a leading
    # backslash to the user's home drive, which is what %HOMEPATH% is meant to
    # be relative to.
    if _IS_WIN and expanded[:1] in ("\\", "/") and not Path(expanded).drive:
        drive = os.environ.get("HOMEDRIVE") or (Path.home().drive)
        expanded = f"{drive}{expanded}"
    p = Path(expanded)
    return p


def _is_protected_root(target: Path) -> bool:
    """Refuse to recursively destroy a drive root or the home folder itself.

    This is the single guard kept from the sandboxed file_controller. It is not
    about permissions — it is about intent: 'delete C:\\' is never what a person
    meant, and no undo can put it back.
    """
    try:
        resolved = target.resolve()
    except Exception:
        return False
    if resolved == Path.home().resolve():
        return True
    # A drive root on Windows ("C:\\") or the filesystem root on POSIX ("/").
    if resolved.parent == resolved:
        return True
    return False


def _fmt_size(b: int) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if b < 1024:
            return f"{b:.1f} {unit}"
        b /= 1024
    return f"{b:.1f} TB"


def _snapshot(path: Path) -> tuple[str | None, bool]:
    """Return (previous_text_or_None, undoable). None means the file did not
    exist yet, which is a different undo (delete it) from an edit."""
    if not path.exists():
        return None, True
    try:
        if path.stat().st_size > _UNDO_CONTENT_LIMIT:
            return None, False
        return path.read_text(encoding="utf-8", errors="ignore"), True
    except Exception:
        return None, False


# ── file operations (unrestricted) ───────────────────────────────────────────

def read_any(path: str, max_chars: int = 0, tail: bool = False) -> str:
    """Read a file anywhere on the machine. 0 = whole file. tail=True reads the
    end (the useful half of a log)."""
    try:
        target = _expand(path)
        if not target.exists():
            return f"File not found: {target}"
        if target.is_dir():
            return list_dir(str(target))
        content = target.read_text(encoding="utf-8", errors="replace")
        total = len(content)
        if max_chars and total > max_chars:
            content = (content[-max_chars:] if tail else content[:max_chars])
            where = "last" if tail else "first"
            content += f"\n\n[Truncated — showing {where} {max_chars:,} of {total:,} chars]"
        return content
    except Exception as e:
        return f"Could not read {path}: {e}"


def list_dir(path: str, show_hidden: bool = False) -> str:
    try:
        target = _expand(path)
        if not target.exists():
            return f"Path not found: {target}"
        if not target.is_dir():
            return f"Not a directory: {target}"
        # A drive root ("C:\\") or POSIX root has no .name — show the path itself.
        header = f"(folder) {target}" if target.name else f"(folder) {target.resolve()}"
        lines = [header]
        entries = sorted(target.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
        shown = 0
        for item in entries:
            if not show_hidden and item.name.startswith("."):
                continue
            try:
                if item.is_dir():
                    lines.append(f"  📁 {item.name}/")
                else:
                    lines.append(f"  📄 {item.name} ({_fmt_size(item.stat().st_size)})")
                shown += 1
            except Exception:
                lines.append(f"  ?  {item.name} (unreadable)")
        if shown == 0:
            return f"Directory is empty: {target}"
        return f"{shown} item(s):\n" + "\n".join(lines)
    except PermissionError:
        return f"Permission denied: {path} (try an elevated run if this is a system folder)"
    except Exception as e:
        return f"Could not list {path}: {e}"


def write_any(path: str, content: str = "", append: bool = False,
              create_dirs: bool = True) -> str:
    """Write ANY file anywhere, with a snapshot pushed onto the undo stack."""
    try:
        target = _expand(path)
        if target.is_dir():
            return f"That is a directory, not a file: {target}"
        if create_dirs:
            target.parent.mkdir(parents=True, exist_ok=True)

        previous, undoable = _snapshot(target)
        existed = target.exists()

        mode = "a" if append else "w"
        with open(target, mode, encoding="utf-8") as f:
            f.write(content or "")

        if undoable:
            def _undo(p=target, prev=previous, had=existed):
                if not had:
                    if p.exists():
                        p.unlink()
                        return f"Removed '{p.name}' — it did not exist before."
                    return f"'{p.name}' is already gone."
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(prev or "", encoding="utf-8")
                return f"Restored '{p.name}'."

            verb = "appended to" if append else "wrote"
            push_undo(f"{verb} {target.name}", _undo)

        verb = "Appended to" if append else "Written to"
        note = "" if undoable else " (too large to snapshot — not undoable)"
        return f"{verb}: {target}{note}"
    except PermissionError:
        return (f"Permission denied: {path}. System folders may need the app to "
                f"run as administrator.")
    except Exception as e:
        return f"Could not write {path}: {e}"


def delete_any(path: str, permanent: bool = False) -> str:
    """Delete a file or folder anywhere. Recycle Bin by default so it is
    recoverable; permanent=True uses rmtree/unlink."""
    try:
        target = _expand(path)
        if not target.exists():
            return f"Not found: {target}"
        if _is_protected_root(target):
            return (f"Refusing to delete {target} — that is a drive or home root, "
                    f"and nothing can put it back.")

        if not permanent:
            try:
                import send2trash
                send2trash.send2trash(str(target))
                return f"Moved to Recycle Bin: {target.name}"
            except ImportError:
                pass  # fall through to permanent with a clear note

        if target.is_dir():
            shutil.rmtree(target)
        else:
            target.unlink()
        return f"Permanently deleted: {target}" + ("" if permanent else " (send2trash unavailable)")
    except Exception as e:
        return f"Could not delete {path}: {e}"


def make_dir(path: str) -> str:
    try:
        target = _expand(path)
        if target.exists() and not target.is_dir():
            return f"A file already exists at that path: {target}"
        existed = target.exists()
        target.mkdir(parents=True, exist_ok=True)
        if not existed:
            def _undo(p=target):
                try:
                    if p.exists() and p.is_dir() and not any(p.iterdir()):
                        p.rmdir()
                        return f"Removed folder '{p.name}'."
                    return f"'{p.name}' is not empty — left alone."
                except Exception as e:
                    return f"Could not remove folder: {e}"
            push_undo(f"created folder {target.name}", _undo)
        return f"Folder ready: {target}"
    except Exception as e:
        return f"Could not create folder {path}: {e}"


def move_any(source: str, destination: str) -> str:
    try:
        src = _expand(source)
        dst = _expand(destination)
        if not src.exists():
            return f"Source not found: {src}"
        if not destination or not str(destination).strip():
            return "No destination given."
        # Moving a folder into itself or into its own child would recurse —
        # shutil.move raises a confusing error; say what is wrong instead.
        if src.is_dir():
            try:
                if dst.resolve() == src.resolve() or dst.resolve().is_relative_to(src.resolve()):
                    return (f"Cannot move '{src.name}' into itself "
                            f"({dst}) — that would put the folder inside its own copy.")
            except Exception:
                pass
        if dst.is_dir():
            dst = dst / src.name
        dst.parent.mkdir(parents=True, exist_ok=True)
        origin = src.resolve()
        shutil.move(str(src), str(dst))

        def _undo(a=origin, b=dst.resolve()):
            if not b.exists():
                return f"'{b.name}' is no longer there."
            a.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(b), str(a))
            return f"'{a.name}' is back in {a.parent}."

        push_undo(f"moved {origin.name}", _undo)
        return f"Moved: {src} → {dst}"
    except Exception as e:
        return f"Could not move: {e}"


def copy_any(source: str, destination: str) -> str:
    try:
        src = _expand(source)
        dst = _expand(destination)
        if not src.exists():
            return f"Source not found: {src}"
        if not destination or not str(destination).strip():
            return "No destination given."
        if dst.is_dir():
            dst = dst / src.name
        # Copying a folder into its own subtree is an infinite recursion. Refuse
        # it explicitly instead of letting copytree die halfway through.
        if src.is_dir():
            try:
                if dst.resolve().is_relative_to(src.resolve()):
                    return (f"Cannot copy '{src.name}' into itself "
                            f"({dst}) — that would never finish.")
            except Exception:
                pass
        dst.parent.mkdir(parents=True, exist_ok=True)
        if src.is_dir():
            shutil.copytree(str(src), str(dst))
        else:
            shutil.copy2(str(src), str(dst))

        made = dst.resolve()

        def _undo(p=made):
            try:
                if not p.exists():
                    return f"'{p.name}' is already gone."
                shutil.rmtree(p) if p.is_dir() else p.unlink()
                return f"Removed the copy '{p.name}'."
            except Exception as e:
                return f"Could not remove copy: {e}"

        push_undo(f"copied {src.name}", _undo)
        return f"Copied: {src} → {dst}"
    except Exception as e:
        return f"Could not copy: {e}"


def search_files(query: str, root: str = "", extension: str = "",
                 max_results: int = 30) -> str:
    """Search the WHOLE filesystem (or a given root) for a name fragment.
    Skips the usual noise directories so a full-disk search stays fast."""
    try:
        base = _expand(root) if root else Path.home()
        if not base.exists():
            return f"Search root not found: {base}"

        skip = {"node_modules", ".git", "__pycache__", "AppData", "$Recycle.Bin",
                "System Volume Information", ".venv", "venv", "site-packages",
                "Windows", "ProgramData"}
        hits: list[str] = []
        scanned = 0
        for dirpath, dirnames, filenames in os.walk(base, topdown=True):
            dirnames[:] = [d for d in dirnames if d not in skip and not d.startswith(".")]
            for fn in filenames:
                scanned += 1
                if extension and not fn.lower().endswith(extension.lower()):
                    continue
                if query and query.lower() not in fn.lower():
                    continue
                full = Path(dirpath) / fn
                try:
                    hits.append(f"{full}  ({_fmt_size(full.stat().st_size)})")
                except Exception:
                    hits.append(str(full))
                if len(hits) >= max_results:
                    break
            if len(hits) >= max_results:
                break

        if not hits:
            return (f"No file matching '{query or extension or '*'}' under {base} "
                    f"({scanned:,} files scanned).")
        return (f"Found {len(hits)} match(es) for '{query or extension}' "
                f"({scanned:,} scanned):\n" + "\n".join(hits))
    except Exception as e:
        return f"Search error: {e}"


def grep_files(pattern: str, root: str = "", extension: str = ".py",
               max_results: int = 40) -> str:
    """Search file CONTENTS anywhere. The recursive-content twin of search_files."""
    if not pattern or not pattern.strip():
        # An empty needle matches every line of every file — refuse rather than
        # burn a full-disk walk producing useless output.
        return "No search pattern given. Provide 'query' with the text to find."
    try:
        base = _expand(root) if root else Path.home()
        needle = pattern.lower()
        skip = {"node_modules", ".git", "__pycache__", ".venv", "venv",
                "site-packages", "System Volume Information", "Windows"}
        hits: list[str] = []
        for dirpath, dirnames, filenames in os.walk(base, topdown=True):
            dirnames[:] = [d for d in dirnames if d not in skip and not d.startswith(".")]
            for fn in filenames:
                if extension and not fn.lower().endswith(extension.lower()):
                    continue
                full = Path(dirpath) / fn
                try:
                    if full.stat().st_size > 2_000_000:
                        continue
                    with open(full, "r", encoding="utf-8", errors="ignore") as f:
                        for i, line in enumerate(f, 1):
                            if needle in line.lower():
                                hits.append(f"{full}:{i}: {line.strip()[:160]}")
                                break
                except Exception:
                    continue
                if len(hits) >= max_results:
                    break
            if len(hits) >= max_results:
                break
        if not hits:
            return f"No file containing '{pattern}' under {base}."
        return f"{len(hits)} file(s) contain '{pattern}':\n" + "\n".join(hits)
    except Exception as e:
        return f"Content search error: {e}"


# ── application launching (beyond the alias map) ─────────────────────────────

def launch(path_or_name: str, args: str = "") -> str:
    """Launch anything: an alias ('chrome'), an executable name, or a full path
    to a .exe/.app/.desktop, optionally with arguments."""
    target = (path_or_name or "").strip().strip('"')
    if not target:
        return "No application given."
    try:
        expanded = _expand(target)
        if expanded.exists():
            if _IS_WIN and expanded.suffix.lower() not in (".exe", ".bat", ".cmd", ".com"):
                os.startfile(str(expanded))          # let Windows pick the handler
                return f"Opened: {expanded}"
            argv = [str(expanded)] + (args.split() if args else [])
            subprocess.Popen(argv, close_fds=True)
            return f"Launched: {expanded}" + (f" {' '.join(argv[1:])}" if len(argv) > 1 else "")

        # Bare name — let the OS resolve it from PATH / Start menu / Spotlight.
        if args:
            found_args = shutil.which(target) or shutil.which(target.split(".")[0])
            if found_args:
                subprocess.Popen([found_args] + args.split(), close_fds=True)
                return f"Launched: {target} {args}"
            # A name with flags ("code --new-window") — hand the whole thing to
            # the shell resolver only when it is not simply an app plus args.
            subprocess.Popen(target.split() + args.split(), close_fds=True)
            return f"Launched: {target} {args}"

        found = shutil.which(target) or shutil.which(target.split(".")[0])
        if found:
            subprocess.Popen([found], close_fds=True)
            return f"Launched: {target}"

        if _IS_WIN:
            os.startfile(target)                      # shell association / UWP apps
            time.sleep(0.6)
            return f"Requested Windows launch: {target}"
        if _OS == "Darwin":
            subprocess.run(["open", "-a", target], capture_output=True, timeout=10)
            return f"Opened application: {target}"
        subprocess.run(["xdg-open", target], capture_output=True, timeout=10)
        return f"Opened via xdg-open: {target}"
    except Exception as e:
        return f"Could not launch '{target}': {e}"


# ── shell execution ──────────────────────────────────────────────────────────

def run_command(command: str, cwd: str = "", timeout: int = 30,
                elevated: bool = False) -> str:
    """Run a shell command and return its output. This is the escape hatch that
    makes the assistant genuinely able to fix things on the machine."""
    if not command or not command.strip():
        return "No command given."
    low = command.lower()
    for bad in _FORBIDDEN_COMMAND_FRAGMENTS:
        if bad in low:
            return f"Refused: '{bad.strip()}' can destroy this machine and cannot be undone."

    workdir = str(_expand(cwd)) if cwd else None
    if workdir and not Path(workdir).exists():
        return f"Working directory not found: {workdir}"

    timeout = max(1, min(int(timeout), 300))
    try:
        if elevated and _IS_WIN:
            inner = command
            full = ["powershell", "-NoProfile", "-Command",
                    f'Start-Process powershell -Verb RunAs -ArgumentList '
                    f'\'-NoProfile\',\'-Command\',"{inner}"']
            subprocess.Popen(full, close_fds=True)
            return f"Launched elevated (UAC prompt on screen): {command}"

        if _IS_WIN:
            argv = ["powershell", "-NoProfile", "-NonInteractive", "-Command", command]
        else:
            argv = ["/bin/bash", "-lc", command]

        proc = subprocess.run(
            argv, capture_output=True, text=True,
            encoding="utf-8", errors="replace",
            timeout=timeout, cwd=workdir,
        )
        out = (proc.stdout or "").strip()
        err = (proc.stderr or "").strip()
        parts = []
        if out:
            parts.append(f"stdout:\n{out[:4000]}")
        if err:
            parts.append(f"stderr:\n{err[:2000]}")
        parts.append(f"(exit code {proc.returncode})")
        return "\n\n".join(parts)
    except subprocess.TimeoutExpired:
        return f"Command timed out after {timeout}s."
    except FileNotFoundError as e:
        return f"Interpreter/shell not found: {e}"
    except Exception as e:
        return f"Command failed: {e}"


# ── system inspection ────────────────────────────────────────────────────────

def system_info() -> str:
    """Everything about this machine a person might ask for, in one call."""
    lines = [
        f"OS         : {platform.system()} {platform.release()} "
        f"({platform.version()})",
        f"Architecture: {platform.machine()}",
        f"Hostname   : {platform.node()}",
        f"User       : {os.environ.get('USERNAME') or os.environ.get('USER') or '?'}",
        f"Python     : {sys.version.split()[0]}  ({sys.executable})",
        f"CPU        : {platform.processor() or 'unknown'}",
        f"CWD        : {os.getcwd()}",
    ]
    try:
        import psutil
        vm = psutil.virtual_memory()
        lines.append(f"RAM        : {_fmt_size(vm.used)} used / "
                     f"{_fmt_size(vm.total)} total ({vm.percent}%)")
        lines.append(f"CPU cores  : {psutil.cpu_count(logical=True)} logical")
        boot = datetime.fromtimestamp(psutil.boot_time())
        lines.append(f"Booted     : {boot:%Y-%m-%d %H:%M}")
    except Exception:
        pass
    return "\n".join(lines)


def drives() -> str:
    """List mounted drives / volumes and their free space."""
    try:
        import psutil
        lines = []
        for part in psutil.disk_partitions(all=False):
            try:
                usage = psutil.disk_usage(part.mountpoint)
                lines.append(
                    f"{part.device}  [{part.fstype or '?'}]  "
                    f"{_fmt_size(usage.free)} free of {_fmt_size(usage.total)} "
                    f"({usage.percent}% used)  →  {part.mountpoint}"
                )
            except Exception:
                lines.append(f"{part.device} → {part.mountpoint} (unavailable)")
        return "\n".join(lines) if lines else "No drives reported."
    except Exception as e:
        return f"Could not list drives: {e}"


def processes(filter_name: str = "", top: int = 15) -> str:
    """List running processes, optionally filtered, highest CPU first."""
    try:
        import psutil
        rows = []
        for p in psutil.process_iter(["pid", "name", "cpu_percent", "memory_percent"]):
            try:
                info = p.info
                name = info.get("name") or ""
                if filter_name and filter_name.lower() not in name.lower():
                    continue
                rows.append((info.get("cpu_percent") or 0.0,
                             info.get("memory_percent") or 0.0,
                             info.get("pid"), name))
            except Exception:
                continue
        rows.sort(reverse=True)
        rows = rows[:max(1, min(int(top), 60))]
        if not rows:
            return f"No processes matched '{filter_name}'." if filter_name else "No processes."
        lines = [f"{'CPU%':>6} {'MEM%':>6}  {'PID':>7}  NAME"]
        for cpu, mem, pid, name in rows:
            lines.append(f"{cpu:>6.1f} {mem:>6.1f}  {pid:>7}  {name}")
        return "\n".join(lines)
    except Exception as e:
        return f"Could not list processes: {e}"


def env_var(name: str = "") -> str:
    """Read one environment variable, or every one if no name is given."""
    if name:
        value = os.environ.get(name)
        return f"{name} = {value}" if value is not None else f"{name} is not set."
    return "\n".join(f"{k}={v}" for k, v in sorted(os.environ.items()))


def path_info(path: str) -> str:
    """Stat anything: size, timestamps, type, permissions, and — for a folder —
    how many items it holds and how large it is in total."""
    try:
        target = _expand(path)
        if not target.exists():
            return f"Not found: {target}"
        st = target.stat()
        kind = "Folder" if target.is_dir() else "File"
        lines = [
            f"Path       : {target.resolve()}",
            f"Type       : {kind}",
            f"Size       : {_fmt_size(st.st_size)}",
            f"Created    : {datetime.fromtimestamp(st.st_ctime):%Y-%m-%d %H:%M}",
            f"Modified   : {datetime.fromtimestamp(st.st_mtime):%Y-%m-%d %H:%M}",
            f"Read-only  : {not os.access(target, os.W_OK)}",
        ]
        if target.is_file():
            lines.append(f"Extension  : {target.suffix or '—'}")
            mime = None
            try:
                import mimetypes
                mime = mimetypes.guess_type(str(target))[0]
            except Exception:
                pass
            if mime:
                lines.append(f"MIME type  : {mime}")
        else:
            files = dirs = 0
            total = 0
            for dirpath, dirnames, filenames in os.walk(target):
                dirs += len(dirnames)
                for fn in filenames:
                    files += 1
                    try:
                        total += (Path(dirpath) / fn).stat().st_size
                    except Exception:
                        pass
            lines.append(f"Contains   : {files} file(s), {dirs} folder(s)")
            lines.append(f"Total size : {_fmt_size(total)}")
        return "\n".join(lines)
    except PermissionError:
        return f"Permission denied: {path}"
    except Exception as e:
        return f"Could not stat {path}: {e}"


def disk_cleanup(path: str, extension: str = "", min_age_days: int = 0,
                 permanent: bool = False, dry_run: bool = True) -> str:
    """Free space: find (and optionally trash) junk files under a folder.

    Ships DRY-RUN by default — it reports what it would remove and removes
    nothing until the caller explicitly passes dry_run=False. A cleanup that
    guesses wrong and runs anyway is how an assistant deletes somebody's work.
    """
    try:
        base = _expand(path)
        if not base.exists() or not base.is_dir():
            return f"Not a folder: {base}"
        if _is_protected_root(base):
            return f"Refusing to sweep a drive/home root ({base}) — name a folder."

        junk_ext = {extension.lower()} if extension else \
            {".tmp", ".temp", ".log", ".bak", ".old", ".dmp", ".crdownload",
             ".part", ".cache"}
        cutoff = time.time() - (min_age_days * 86400 if min_age_days else 0)

        candidates: list[Path] = []
        total = 0
        for dirpath, dirnames, filenames in os.walk(base, topdown=True):
            dirnames[:] = [d for d in dirnames if d not in {"__pycache__", ".git"}]
            for fn in filenames:
                full = Path(dirpath) / fn
                if full.suffix.lower() not in junk_ext:
                    continue
                try:
                    st = full.stat()
                except Exception:
                    continue
                if st.st_mtime < cutoff:
                    candidates.append(full)
                    total += st.st_size

        if not candidates:
            return f"Nothing to clean under {base} for {sorted(junk_ext)}."

        listing = "\n".join(
            f"  {_fmt_size(p.stat().st_size):>10}  {p}" for p in candidates[:25])
        more = (f"\n  ... and {len(candidates) - 25} more"
                if len(candidates) > 25 else "")

        if dry_run:
            return (f"DRY RUN — {len(candidates)} file(s), {_fmt_size(total)} "
                    f"recoverable under {base}:\n{listing}{more}\n\n"
                    f"Run again with dry_run=false to actually remove them.")

        removed = 0
        for p in candidates:
            try:
                if permanent:
                    p.unlink()
                else:
                    import send2trash
                    send2trash.send2trash(str(p))
                removed += 1
            except Exception:
                continue
        verb = "permanently deleted" if permanent else "moved to the Recycle Bin"
        return (f"Cleaned {removed}/{len(candidates)} file(s), "
                f"~{_fmt_size(total)} {verb}.")
    except Exception as e:
        return f"Cleanup failed: {e}"


def kill_process(name: str = "", pid: int = 0, force: bool = False) -> str:
    """End a process by name or PID. The counterpart to processes()."""
    try:
        import psutil
    except ImportError:
        return "psutil is not installed — cannot manage processes."

    targets = []
    if pid:
        try:
            targets = [psutil.Process(int(pid))]
        except Exception as e:
            return f"No process with PID {pid}: {e}"
    elif name:
        for p in psutil.process_iter(["pid", "name"]):
            try:
                if name.lower() in (p.info.get("name") or "").lower():
                    targets.append(p)
            except Exception:
                continue
    else:
        return "Give a process name or a PID to end."

    if not targets:
        return f"No running process matched '{name or pid}'."

    ended, failed = [], []
    for p in targets:
        try:
            pname = p.name()
            p.kill() if force else p.terminate()
            ended.append(f"{pname} (pid {p.pid})")
        except Exception as e:
            failed.append(f"pid {getattr(p, 'pid', '?')}: {e}")

    out = []
    if ended:
        out.append("Ended: " + ", ".join(ended))
    if failed:
        out.append("Could not end: " + ", ".join(failed))
    return "\n".join(out)


def clipboard(text: str = "", action: str = "read") -> str:
    """Read or write the system clipboard. `action` is 'read' or 'write'."""
    act = (action or "read").lower().strip()
    try:
        import pyperclip
    except ImportError:
        return "pyperclip is not installed — run: pip install pyperclip"
    try:
        if act in ("write", "set", "copy", "paste"):
            pyperclip.copy(text or "")
            shown = (text or "")[:80]
            return f"Clipboard set: {shown}{'…' if len(text or '') > 80 else ''}"
        content = pyperclip.paste() or ""
        if not content.strip():
            return "The clipboard is empty."
        return content
    except Exception as e:
        return f"Clipboard {act} failed: {e}"


def open_with(path: str, app: str = "") -> str:
    """Open a file in its default program, or in a named application."""
    try:
        target = _expand(path)
        if not target.exists():
            return f"Not found: {target}"
        if not app:
            if _IS_WIN:
                os.startfile(str(target))
            elif _OS == "Darwin":
                subprocess.Popen(["open", str(target)], close_fds=True)
            else:
                subprocess.Popen(["xdg-open", str(target)], close_fds=True)
            return f"Opened {target.name} in its default application."

        exe = shutil.which(app) or shutil.which(app.split(".")[0])
        if not exe:
            return f"Could not find the application '{app}'."
        subprocess.Popen([exe, str(target)], close_fds=True)
        return f"Opened {target.name} with {app}."
    except Exception as e:
        return f"Could not open {path}: {e}"


def zip_any(sources: str, destination: str = "") -> str:
    """Compress files/folders into a .zip. `sources` may be one path or several
    separated by ';' or newlines."""
    import zipfile
    try:
        items = [s.strip() for s in re.split(r"[;\n]", sources or "") if s.strip()]
        if not items:
            return "Nothing to compress — give one or more paths."
        paths = [_expand(s) for s in items]
        missing = [str(p) for p in paths if not p.exists()]
        if missing:
            return "Not found: " + ", ".join(missing)

        if destination:
            out = _expand(destination)
            if out.is_dir():
                out = out / "archive.zip"
        else:
            out = paths[0].parent / f"{paths[0].stem}.zip"
        if out.suffix.lower() != ".zip":
            out = out.with_suffix(".zip")
        out.parent.mkdir(parents=True, exist_ok=True)

        added = 0
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
            for p in paths:
                if p.is_dir():
                    for f in p.rglob("*"):
                        if f.is_file():
                            z.write(f, f.relative_to(p.parent))
                            added += 1
                else:
                    z.write(p, p.name)
                    added += 1
        return f"Created {out.name} — {added} file(s), {_fmt_size(out.stat().st_size)}."
    except Exception as e:
        return f"Could not compress: {e}"


def unzip_any(path: str, destination: str = "") -> str:
    """Extract a .zip anywhere, with zip-slip protection."""
    import zipfile
    try:
        src = _expand(path)
        if not src.is_file():
            return f"Not a file: {src}"
        dest = _expand(destination) if destination else src.parent / src.stem
        dest.mkdir(parents=True, exist_ok=True)
        base = dest.resolve()
        extracted = 0
        with zipfile.ZipFile(src) as z:
            for member in z.namelist():
                # Never let an entry escape the destination via ../ or an
                # absolute path — that is the classic zip-slip attack. Compare
                # resolved parents, not a string prefix: base="/a/data" would
                # wrongly accept a sibling "/a/data_evil" under startswith().
                target = (base / member).resolve()
                if target != base and base not in target.parents:
                    return f"Refused: '{member}' would extract outside {dest}."
            z.extractall(dest)
            extracted = len(z.namelist())
        return f"Extracted {extracted} file(s) to {dest}."
    except Exception as e:
        return f"Could not extract: {e}"


def checksum(path: str, algorithm: str = "sha256") -> str:
    """Hash a file — for verifying a download or spotting a duplicate."""
    import hashlib
    try:
        algo = (algorithm or "sha256").lower()
        if algo not in hashlib.algorithms_available:
            return f"Unknown hash algorithm '{algorithm}'."
        target = _expand(path)
        if not target.is_file():
            return f"Not a file: {target}"
        h = hashlib.new(algo)
        with open(target, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        return f"{algo}: {h.hexdigest()}\n  {target}"
    except Exception as e:
        return f"Could not hash: {e}"


def find_duplicates(path: str, max_results: int = 20) -> str:
    """Find duplicate files under a folder by size then content hash."""
    import hashlib
    try:
        base = _expand(path)
        if not base.is_dir():
            return f"Not a folder: {base}"
        by_size: dict[int, list[Path]] = {}
        for dirpath, dirnames, filenames in os.walk(base, topdown=True):
            dirnames[:] = [d for d in dirnames if d not in {".git", "__pycache__", "node_modules"}]
            for fn in filenames:
                full = Path(dirpath) / fn
                try:
                    size = full.stat().st_size
                except Exception:
                    continue
                if size > 0:
                    by_size.setdefault(size, []).append(full)

        groups: list[str] = []
        for size, files in by_size.items():
            if len(files) < 2:
                continue
            by_hash: dict[str, list[Path]] = {}
            for full in files:
                try:
                    h = hashlib.sha256()
                    with open(full, "rb") as f:
                        for chunk in iter(lambda: f.read(1 << 20), b""):
                            h.update(chunk)
                    by_hash.setdefault(h.hexdigest(), []).append(full)
                except Exception:
                    continue
            for digest, dupes in by_hash.items():
                if len(dupes) > 1:
                    groups.append(f"{_fmt_size(size)} × {len(dupes)}  ({digest[:12]})\n"
                                  + "\n".join(f"    {d}" for d in dupes))
                    if len(groups) >= max_results:
                        break
            if len(groups) >= max_results:
                break

        if not groups:
            return f"No duplicate files found under {base}."
        return f"{len(groups)} set(s) of duplicates:\n\n" + "\n\n".join(groups)
    except Exception as e:
        return f"Duplicate scan failed: {e}"


def recent_files(path: str = "", count: int = 20, since_days: int = 7) -> str:
    """What changed recently — the fastest way to find the file you were just
    working on."""
    try:
        base = _expand(path) if path else Path.home()
        if not base.is_dir():
            return f"Not a folder: {base}"
        cutoff = time.time() - (since_days * 86400)
        rows: list[tuple[float, Path]] = []
        skip = {"node_modules", ".git", "__pycache__", "AppData", "site-packages"}
        for dirpath, dirnames, filenames in os.walk(base, topdown=True):
            dirnames[:] = [d for d in dirnames if d not in skip and not d.startswith(".")]
            for fn in filenames:
                full = Path(dirpath) / fn
                try:
                    mtime = full.stat().st_mtime
                except Exception:
                    continue
                if mtime >= cutoff:
                    rows.append((mtime, full))
        rows.sort(reverse=True)
        rows = rows[:max(1, min(int(count), 100))]
        if not rows:
            return f"No files modified in the last {since_days} day(s) under {base}."
        return f"{len(rows)} file(s) changed in the last {since_days} day(s):\n" + \
            "\n".join(f"  {datetime.fromtimestamp(m):%Y-%m-%d %H:%M}  {p}"
                      for m, p in rows)
    except Exception as e:
        return f"Could not list recent files: {e}"


# ── self-repair: JARVIS fixing its own code ──────────────────────────────────

def self_locate(component: str = "") -> str:
    """Where does JARVIS live? Lists its own source tree so it can read, edit
    and reason about itself."""
    root = _project_root()
    if component:
        target = _expand(component)
        if not target.is_absolute():
            # Try a bare filename against the project tree.
            for hit in root.rglob(target.name):
                return f"{hit}  ({_fmt_size(hit.stat().st_size)})"
            return f"No file named '{component}' inside {root}."
        return f"{target}  exists={target.exists()}"
    count = 0
    lines = [f"Application root: {root}"]
    for p in sorted(root.rglob("*.py")):
        if any(part in {".venv", "venv", "__pycache__", "node_modules"} for part in p.parts):
            continue
        lines.append(f"  {p.relative_to(root)}")
        count += 1
        if count >= 200:
            lines.append("  ... (truncated)")
            break
    return "\n".join(lines)


def self_test(timeout: int = 120) -> str:
    """Run the project's own test suite so JARVIS can see whether a change it
    just made broke something."""
    root = _project_root()
    tests_dir = root / "tests"
    if not tests_dir.exists():
        return f"No tests/ directory under {root}."

    args = [sys.executable, "-m", "pytest", "-q", str(tests_dir)]
    try:
        proc = subprocess.run(
            args, capture_output=True, text=True,
            encoding="utf-8", errors="replace",
            timeout=max(10, min(int(timeout), 600)), cwd=str(root),
        )
        tail = "\n".join((proc.stdout or "").splitlines()[-40:])
        verdict = "PASSED" if proc.returncode == 0 else "FAILED"
        return (f"Self-test {verdict} (exit {proc.returncode}).\n\n{tail}\n\n"
                f"{((proc.stderr or '').strip()[-1500:])}")
    except subprocess.TimeoutExpired:
        return f"Self-test timed out after {timeout}s."
    except Exception as e:
        return f"Could not run self-test: {e}"


def self_fix(file_path: str, error: str = "",
             instruction: str = "", backup: bool = True) -> str:
    """Repair a source file JARVIS owns (or any file) using Gemini, and prove the
    fix with a compile check. The whole point of 'fix your own errors'."""
    target = _expand(file_path)
    if not target.is_absolute():
        candidate = _project_root() / file_path
        target = candidate if candidate.exists() else target
    if not target.exists():
        return f"File not found: {target}"

    if target.suffix.lower() != ".py":
        return (f"self_fix currently repairs Python files; '{target.name}' is "
                f"{target.suffix or 'extensionless'}. Use write for a manual edit.")

    try:
        original = target.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        return f"Could not read {target}: {e}"

    api_key = ""
    try:
        api_key = json.loads(_config_path().read_text(encoding="utf-8")) \
            .get("gemini_api_key", "")
    except Exception:
        pass
    if not api_key:
        return "No Gemini API key configured — cannot call a model to reason about the fix."

    problem = (error or "").strip() or "The file is failing; find and fix the bug."
    ask = (instruction or "").strip()

    prompt = f"""You are repairing a Python source file inside a live application.
Return ONLY the complete corrected file. No explanation, no markdown fences.

File: {target.name}
Reported problem / traceback:
{problem[:4000]}
{f"Additional instruction: {ask}" if ask else ""}

Rules:
- Fix the root cause, not the symptom.
- Preserve every existing feature; do not delete working code.
- Keep imports, style and structure consistent with the original.
- The result MUST be valid Python 3.10+.

Current contents:
{original}

Corrected file:"""

    try:
        from core import gemini
        resp = gemini.call(prompt, tier=gemini.SMART, timeout_ms=60_000)
        if resp is None:
            return "Every model on the ladder failed — could not reach Gemini."
        fixed = (resp.text or "").strip()
        if fixed.startswith("```"):
            fixed = fixed.split("\n", 1)[1] if "\n" in fixed else fixed
            fixed = fixed.rsplit("```", 1)[0].strip()
    except Exception as e:
        return f"Fix generation failed: {e}"

    # Prove it compiles before we overwrite a working file with a broken one.
    try:
        compile(fixed, str(target), "exec")
    except SyntaxError as e:
        return (f"Refused to write the fix — the generated code does not compile "
                f"(line {e.lineno}: {e.msg}). The original file is untouched.")

    backup_path = None
    if backup:
        backup_path = target.with_suffix(target.suffix + f".bak_{int(time.time())}")
        backup_path.write_text(original, encoding="utf-8")

    try:
        target.write_text(fixed, encoding="utf-8")
    except Exception as e:
        return f"Could not write the fix: {e}"

    def _undo(p=target, prev=original):
        p.write_text(prev, encoding="utf-8")
        return f"'{p.name}' restored to its previous version."

    push_undo(f"self-fixed {target.name}", _undo)

    before = len(original.splitlines())
    after = len(fixed.splitlines())
    return (f"Repaired {target.name}: {before} → {after} lines. "
            f"Compiles cleanly. Original kept at {backup_path.name}."
            if backup_path else
            f"Repaired {target.name}: {before} → {after} lines. Compiles cleanly.")


def self_repair_all(timeout: int = 120) -> str:
    """The full autonomous loop: run the tests, and if anything fails, look at
    the failures and say which files need attention. Acts as the entry point for
    'fix your own errors'."""
    result = self_test(timeout=timeout)
    if "PASSED" in result.splitlines()[0]:
        return result
    return result + ("\n\nTriage: the failing files above can be repaired one at a "
                     "time with action='self_fix' and file_path=<the failing file>.")


# ── registry / dispatch ──────────────────────────────────────────────────────

_ACTIONS = {
    "read":        lambda p: read_any(p.get("path", ""),
                                      max_chars=int(p.get("max_chars", 0) or 0),
                                      tail=bool(p.get("tail", False))),
    "list":        lambda p: list_dir(p.get("path", "."),
                                      show_hidden=bool(p.get("show_hidden", False))),
    "write":       lambda p: write_any(p.get("path", ""), p.get("content", ""),
                                       append=bool(p.get("append", False))),
    "delete":      lambda p: delete_any(p.get("path", ""),
                                        permanent=bool(p.get("permanent", False))),
    "mkdir":       lambda p: make_dir(p.get("path", "")),
    "move":        lambda p: move_any(p.get("path", ""), p.get("destination", "")),
    "copy":        lambda p: copy_any(p.get("path", ""), p.get("destination", "")),
    "search":      lambda p: search_files(p.get("query", p.get("name", "")),
                                          root=p.get("path", ""),
                                          extension=p.get("extension", ""),
                                          max_results=int(p.get("max_results", 30))),
    "grep":        lambda p: grep_files(p.get("query", p.get("content", "")),
                                        root=p.get("path", ""),
                                        extension=p.get("extension", ".py")),
    "launch":      lambda p: launch(p.get("path", p.get("app", "")), p.get("args", "")),
    "open_with":   lambda p: open_with(p.get("path", ""), p.get("app", "")),
    "run":         lambda p: run_command(p.get("command", ""), cwd=p.get("cwd", ""),
                                         timeout=int(p.get("timeout", 30)),
                                         elevated=bool(p.get("elevated", False))),
    "system_info": lambda p: system_info(),
    "drives":      lambda p: drives(),
    "processes":   lambda p: processes(p.get("query", p.get("name", "")),
                                       top=int(p.get("count", 15))),
    "kill":        lambda p: kill_process(p.get("query", p.get("name", "")),
                                          pid=int(p.get("count", 0) or 0),
                                          force=bool(p.get("permanent", False))),
    "path_info":   lambda p: path_info(p.get("path", "")),
    "env":         lambda p: env_var(p.get("query", p.get("name", ""))),
    "clipboard":   lambda p: clipboard(p.get("content", ""),
                                       p.get("query", "read") or "read"),
    "zip":         lambda p: zip_any(p.get("path", ""), p.get("destination", "")),
    "unzip":       lambda p: unzip_any(p.get("path", ""), p.get("destination", "")),
    "cleanup":     lambda p: disk_cleanup(p.get("path", ""),
                                          extension=p.get("extension", ""),
                                          min_age_days=int(p.get("max_chars", 0) or 0),
                                          permanent=bool(p.get("permanent", False)),
                                          dry_run=not bool(p.get("append", False))),
    "checksum":    lambda p: checksum(p.get("path", ""),
                                      p.get("query", "sha256") or "sha256"),
    "duplicates":  lambda p: find_duplicates(p.get("path", ""),
                                             max_results=int(p.get("count", 20))),
    "recent":      lambda p: recent_files(p.get("path", ""),
                                          count=int(p.get("count", 20)),
                                          since_days=int(p.get("max_chars", 7) or 7)),
    "self_locate": lambda p: self_locate(p.get("path", "")),
    "self_test":   lambda p: self_test(timeout=int(p.get("timeout", 120))),
    "self_fix":    lambda p: self_fix(p.get("path", ""), error=p.get("content", ""),
                                      instruction=p.get("query", "")),
    "self_repair": lambda p: self_repair_all(timeout=int(p.get("timeout", 120))),
}


def system_access(parameters: dict = None, response=None, player=None,
                  session_memory=None, speak=None) -> str:
    p = parameters or {}
    action = str(p.get("action", "")).lower().strip()
    if not action:
        return ("Which action? read, list, write, delete, mkdir, move, copy, search, "
                "grep, launch, run, system_info, drives, processes, env, "
                "self_locate, self_test, self_fix, self_repair.")

    handler = _ACTIONS.get(action)
    if handler is None:
        return (f"Unknown action '{action}'. Valid: "
                f"{', '.join(sorted(_ACTIONS))}.")

    # Advanced Mode gate. Told plainly, because the user can turn it on and the
    # assistant should say exactly what to do rather than fail mysteriously.
    if action in _ADVANCED_ONLY and not _advanced_enabled():
        return ("Advanced Mode is off, so full desktop access is disabled. "
                "Set \"advanced_mode\": true in config/api_keys.json to enable it.")

    if player:
        try:
            target = p.get("path") or p.get("command") or p.get("query") or ""
            player.write_log(f"[access] {action} {str(target)[:60]}")
        except Exception:
            pass

    try:
        return handler(p) or "Done."
    except Exception as e:
        import traceback
        traceback.print_exc()
        return f"system_access '{action}' failed: {e}"


# ── Tool declaration (auto-discovered by core/action_loader.py) ──────────────
TOOL = {
    "name": "system_access",
    "description": (
        "ADVANCED FULL DESKTOP ACCESS — no home-directory sandbox. Read, write, "
        "move, copy, delete, search and grep ANY file or folder on ANY drive. "
        "Launch ANY application or executable by name or full path. Run shell / "
        "PowerShell commands. Inspect system info, drives, processes and "
        "environment variables. Inspect and REPAIR your own source code "
        "(self_locate, self_test, self_fix, self_repair). "
        "Use this whenever file_controller says 'Access denied' because a path is "
        "outside the home folder, when the user names a path on another drive, or "
        "when you need to fix your own code."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": (
                    "read — read any file (path, max_chars, tail) | "
                    "list — list any directory (path, show_hidden) | "
                    "write — write/append any file (path, content, append) | "
                    "delete — delete anything (path, permanent) | "
                    "mkdir — create a folder (path) | "
                    "move — move (path, destination) | "
                    "copy — copy (path, destination) | "
                    "path_info — size, dates, permissions, folder totals (path) | "
                    "search — find files by name across drives (query, path, extension) | "
                    "recent — files changed recently (path, count, max_chars=days) | "
                    "duplicates — find duplicate files by content hash (path, count) | "
                    "grep — search file contents (query, path, extension) | "
                    "checksum — hash a file, sha256/md5 (path, query) | "
                    "launch — start any app/exe (path or app, args) | "
                    "open_with — open a file in its default or a named app (path, app) | "
                    "run — run a shell/PowerShell command (command, cwd, timeout, elevated) | "
                    "kill — end a process by name or PID (query, count=PID, permanent=force) | "
                    "zip — compress files/folders (path, destination) | "
                    "unzip — extract an archive safely (path, destination) | "
                    "cleanup — find/remove junk files; DRY RUN unless append=true (path, extension, permanent) | "
                    "clipboard — read or write the clipboard (query=read|write, content) | "
                    "system_info — OS, user, Python, CPU, RAM | "
                    "drives — mounted volumes and free space | "
                    "processes — running processes (query, count) | "
                    "env — read environment variables (query) | "
                    "self_locate — list your own source tree (path) | "
                    "self_test — run the project test suite | "
                    "self_fix — repair one of your own files (path, content=error, query=instruction) | "
                    "self_repair — run tests and triage failures"
                ),
            },
            "path": {
                "type": "STRING",
                "description": "Target file/folder/app path — absolute or a shortcut (desktop, downloads, home) or app name",
            },
            "destination": {
                "type": "STRING",
                "description": "Destination path for move/copy",
            },
            "content": {
                "type": "STRING",
                "description": "Content to write, or the error/traceback for self_fix",
            },
            "query": {
                "type": "STRING",
                "description": "Search term (search/grep/processes/env), or the instruction for self_fix",
            },
            "command": {
                "type": "STRING",
                "description": "Shell/PowerShell command to execute for action='run'",
            },
            "args": {
                "type": "STRING",
                "description": "Arguments to pass when launching an app",
            },
            "extension": {
                "type": "STRING",
                "description": "File extension filter for search/grep (e.g. .py, .txt)",
            },
            "cwd": {
                "type": "STRING",
                "description": "Working directory for action='run'",
            },
            "timeout": {
                "type": "INTEGER",
                "description": "Timeout in seconds (default 30 for run, 120 for tests)",
            },
            "max_chars": {
                "type": "INTEGER",
                "description": "Max characters to read for action='read' (0 = whole file)",
            },
            "count": {
                "type": "INTEGER",
                "description": "How many results for processes (default 15)",
            },
            "append": {
                "type": "BOOLEAN",
                "description": "Append instead of overwrite for action='write'",
            },
            "tail": {
                "type": "BOOLEAN",
                "description": "Read the END of a file instead of the start (for logs)",
            },
            "permanent": {
                "type": "BOOLEAN",
                "description": "Delete permanently instead of to the Recycle Bin",
            },
            "elevated": {
                "type": "BOOLEAN",
                "description": "Run the command with administrator rights (Windows only, shows UAC)",
            },
            "show_hidden": {
                "type": "BOOLEAN",
                "description": "Include hidden/dot files when listing",
            },
            "app": {
                "type": "STRING",
                "description": "Application to open a file with for action='open_with'",
            },
        },
        "required": ["action"],
    },
    "handler": system_access,
}
