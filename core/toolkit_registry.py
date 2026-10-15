"""
core/toolkit_registry.py — ONE registry over every skill, action and plugin.

WHAT THIS REPLACES
    Two registries doing the same job:

        core/action_loader.py  -> ActionRegistry    (actions/*.py, TOOL dict)
        core/plugin_loader.py  -> PluginRegistry    (plugins/*.py, PLUGIN dict)

    main.py carried a branch for each, the UI carried a third list, and the
    dependency check knew about neither. This module loads BOTH folders through
    the one validator in core/toolkit.py and hands back a single registry that
    answers every question the app actually asks:

        registry.has(name)                    is this a tool I know?
        registry.declaration_list()           what do I tell the model?
        registry.call(name, args, ctx)        run it
        registry.scheduling(name)             when may its result re-enter?
        registry.ui_list()                    what does the manager render?
        registry.dependency_report()          what is this machine missing?

SCAN ORDER AND COLLISIONS
    Folders are scanned in order: skills/, actions/, plugins/. First definition
    of a name wins, and a later duplicate is recorded as rejected with a
    readable reason — never silently dropped, because "why is my plugin not
    running" is exactly the question this file has to answer.

    A name that collides with an inline core tool (passed in as `reserved`) is
    rejected the same way.

ENABLE / DISABLE
    Read from config on every relevant call rather than cached, so toggling a
    plugin in the UI takes effect on the next tool call, with no restart — the
    same behaviour the old plugin loader had, kept deliberately.
"""
from __future__ import annotations

import importlib.util
import sys
import traceback
from pathlib import Path
from typing import Callable, Iterable, Optional

from core.toolkit import SkillSpec, validate

# Which folders are scanned, and what a skill found there is called. Order is
# significant: skills/ is the new home, actions/ and plugins/ are the existing
# ones and keep working exactly as before.
DEFAULT_SOURCES = (
    ("skills",  "skills"),
    ("actions", "actions"),
    ("plugins", "plugins"),
)

# Folders a recursive scan never descends into.
_SKIP_DIRS = {".venv", "venv", "__pycache__", "node_modules", ".git", ".idea"}


def _load_module(path: Path, dotted: str):
    """Import a skill file under a stable module name, reusing an already-loaded
    module so its handlers are the same objects the rest of the app holds."""
    existing = sys.modules.get(dotted)
    if existing is not None:
        return existing
    spec = importlib.util.spec_from_file_location(dotted, path)
    if spec is None or spec.loader is None:
        raise ImportError("could not build import spec")
    module = importlib.util.module_from_spec(spec)
    sys.modules[dotted] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(dotted, None)
        raise
    return module


def _friendly_load_error(exc: Exception, path: Path, folder: str) -> str:
    """Turn an import failure into something the person who downloaded the file
    can act on, rather than a stack trace."""
    if isinstance(exc, ModuleNotFoundError):
        missing = getattr(exc, "name", "") or ""
        parts = missing.split(".")
        # A private helper that ships alongside the skill: name the file to fetch.
        if len(parts) == 2 and parts[0] == folder and parts[1].startswith("_"):
            return (f"Needs the shared file '{parts[1]}.py', which is not in "
                    f"{folder}/. Download it beside {path.name} and restart.")
        if parts and parts[0] not in (folder, "core", "memory"):
            return f"Needs a package that is not installed: pip install {parts[0]}"
    return f"Failed to load: {exc}"


class ToolkitRegistry:
    """The one place the app asks about the things it can do."""

    def __init__(self, skills: dict[str, SkillSpec],
                 logger: Callable[[str], None] = print,
                 notify: Optional[Callable[[str], None]] = None,
                 enabled_fn: Optional[Callable[[str], bool]] = None):
        self._skills = skills                     # name -> SkillSpec, VALID only
        self._all: list[SkillSpec] = []           # valid + rejected, for the UI
        self._logger = logger
        self._notify = notify or (lambda _m: None)
        # Which names may run right now. Defaults to "everything is enabled", so
        # a caller that has no config layer (tests, the builder) still works.
        self._enabled = enabled_fn or (lambda _name: True)

    # ── what the model is told ───────────────────────────────────────────────
    def declaration_list(self) -> list[dict]:
        return [s.declaration() for s in self._skills.values() if self._enabled(s.name)]

    # Aliases so main.py's existing call sites — written against the two old
    # registries — keep working against this one object with no branching.
    def get_tool_declarations(self) -> list[dict]:
        return self.declaration_list()

    def run(self, name: str, parameters: dict, ctx: dict | None = None,
            player=None, session_memory=None) -> str:
        """Compatibility entry point for both old call styles.

        The action loader passed a ctx dict; the plugin loader passed player=
        and session_memory= as keywords. Accepting both means neither call site
        in main.py has to know which kind of skill it is invoking."""
        if ctx is None:
            ctx = {"player": player, "session_memory": session_memory,
                   "speak": None, "response": None}
        return self.call(name, parameters, ctx)

    # ── the questions main.py asks ───────────────────────────────────────────
    def has(self, name: str) -> bool:
        return name in self._skills

    def get(self, name: str) -> Optional[SkillSpec]:
        return self._skills.get(name)

    def names(self) -> set[str]:
        return set(self._skills.keys())

    def scheduling(self, name: str) -> Optional[str]:
        spec = self._skills.get(name)
        return spec.scheduling if spec else None

    def enabled(self, name: str) -> bool:
        return name in self._skills and self._enabled(name)

    def call(self, name: str, parameters: dict, ctx: dict | None = None) -> str:
        spec = self._skills.get(name)
        if spec is None:
            return f"Skill '{name}' is not available."
        if not self._enabled(name):
            return f"The '{name}' skill is currently disabled."
        return spec.call(parameters, ctx)

    # ── the questions the UI asks ────────────────────────────────────────────
    def ui_list(self) -> list[dict]:
        out = []
        for spec in self._all:
            row = spec.to_ui()
            row["enabled"] = self._enabled(spec.name) if spec.valid else False
            out.append(row)
        return out

    def settings_schemas(self) -> list[dict]:
        """One entry per settings SECTION, for enabled skills that declare one.
        Deduped by namespace so a suite of skills sharing a namespace shows a
        single form."""
        from memory.config_manager import get_plugin_config
        seen: set[str] = set()
        out: list[dict] = []
        for spec in self._skills.values():
            if not spec.settings or not self._enabled(spec.name):
                continue
            ns = spec.settings.get("namespace") or spec.name
            if ns in seen:
                continue
            seen.add(ns)
            out.append({
                "plugin":    spec.name,
                "namespace": ns,
                "title":     spec.settings.get("title") or spec.name,
                "fields":    spec.settings.get("fields", []),
                "values":    get_plugin_config(ns),
                "action":    spec.settings.get("action"),
            })
        return out

    # ── dependencies ─────────────────────────────────────────────────────────
    def requirements(self) -> dict[str, list[str]]:
        """Every skill's declared pip needs, keyed by skill name."""
        return {name: list(s.requires) for name, s in self._skills.items() if s.requires}

    def dependency_report(self) -> str:
        """Which skills are missing what — the honest answer to 'why doesn't X
        work?'. Bundled skills come with their needs pre-installed, so this is
        almost always about a downloaded plugin."""
        from core import deps
        lines: list[str] = []
        for name, spec in sorted(self._skills.items()):
            missing = deps.missing_for(spec.requires)
            if missing:
                lines.append(f"  • {name} needs: {', '.join(missing)}")
        base = deps.report(include_optional=False)
        if lines:
            return base + "\n\nSkills with missing dependencies:\n" + "\n".join(lines)
        return base

    def counts(self) -> dict:
        valid = len(self._skills)
        return {
            "valid": valid,
            "rejected": len(self._all) - valid,
            "total": len(self._all),
            "by_source": {
                src: sum(1 for s in self._all if s.source == src)
                for src, _ in DEFAULT_SOURCES
            },
        }


def _iter_files(folder: Path) -> Iterable[Path]:
    """*.py files directly in `folder`, plus one level of subfolders — enough for
    a grouped skill pack without turning startup into a full tree walk."""
    for path in sorted(folder.glob("*.py")):
        yield path
    for sub in sorted(p for p in folder.iterdir() if p.is_dir()):
        if sub.name.startswith((".", "_")) or sub.name in _SKIP_DIRS:
            continue
        for path in sorted(sub.glob("*.py")):
            yield path


def discover_toolkit(root: Path | Iterable[Path],
                     reserved: set[str] | None = None,
                     logger: Callable[[str], None] = print,
                     notify: Optional[Callable[[str], None]] = None,
                     enabled_fn: Optional[Callable[[str], bool]] = None,
                     sources: Iterable[tuple[str, str]] = DEFAULT_SOURCES
                     ) -> ToolkitRegistry:
    """Scan the skill folders and build one registry.

    `root` may be one folder or several. Passing several is what lets an
    INSTALLED copy read the bundled skills from the program folder and the
    user's own additions from AppData in a single registry — the two live in
    different places once the app is installed, and both should be found.
    Earlier roots win: the bundled skills are scanned first, so a user file
    cannot silently replace a shipped one; it is reported as a collision.

    Never raises: a file that fails to import, fails validation, or collides with
    another name is recorded as rejected with a readable reason and the scan
    continues. One bad download must never stop the assistant from starting.
    """
    reserved = reserved or set()
    roots = [root] if isinstance(root, (str, Path)) else [r for r in root if r]
    # Materialise `sources` once: it is iterated inside the roots loop below and
    # read again for the summary, so a generator argument would be exhausted.
    sources = list(sources)

    valid: dict[str, SkillSpec] = {}
    all_specs: list[SkillSpec] = []

    for base in roots:
      base = Path(base)
      for folder_name, source in sources:
        folder = base / folder_name
        if not folder.exists():
            # A root that does not have this folder is normal — an installed
            # copy has no user actions/ folder until the user makes one.
            continue

        for path in _iter_files(folder):
            if path.name.startswith("_") or path.name == "__init__.py":
                continue   # templates and shared helpers, never skills themselves

            # A stable module name per file. Keyed on the path so the same file
            # found under two roots cannot masquerade as one module.
            dotted = "skillmod_" + "_".join(
                p for p in path.with_suffix("").parts if p.isidentifier())[-120:]

            try:
                module = _load_module(path, dotted)
                spec = validate(module, path.name, source=source)

                # A file with no declaration block is a helper, not a mistake.
                if not spec.valid and spec.error.startswith("No SKILL / TOOL / PLUGIN"):
                    continue

                if spec.valid and spec.name in reserved:
                    spec = SkillSpec(name=spec.name, file=path.name, source=source,
                                     error=f"Name '{spec.name}' collides with a core "
                                           f"tool — rejected.")
                elif spec.valid and spec.name in valid:
                    other = valid[spec.name].file
                    spec = SkillSpec(name=spec.name, file=path.name, source=source,
                                     error=f"Name '{spec.name}' is already used by "
                                           f"'{other}' — rejected.")

            except Exception as e:
                spec = SkillSpec(name=path.stem, file=path.name, source=source,
                                 error=_friendly_load_error(e, path, folder_name))
                traceback.print_exc()

            all_specs.append(spec)
            if spec.valid:
                valid[spec.name] = spec
                logger(f"[{source}] loaded: {spec.name} ({path.name})")
            else:
                logger(f"[{source}] rejected: {path.name} — {spec.error}")

    registry = ToolkitRegistry(valid, logger, notify, enabled_fn)
    registry._all = all_specs

    counts = registry.counts()
    logger(f"Toolkit ready: {counts['valid']} skill(s) active, "
           f"{counts['rejected']} rejected, across {len(sources)} folder(s).")
    if counts["rejected"]:
        registry._notify(f"{counts['rejected']} skill(s) could not be loaded — "
                         f"see the console.")
    return registry
