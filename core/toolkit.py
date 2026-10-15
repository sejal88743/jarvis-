"""
core/toolkit.py — ONE interface for every skill, action and plugin.

THE PROBLEM THIS SOLVES
    The app grew two parallel, near-identical systems:

        actions/*.py   -> expose a module-level TOOL  dict + a `handler`
        plugins/*.py   -> expose a module-level PLUGIN dict + a `run()`

    Two loaders (core/action_loader.py, core/plugin_loader.py), two validators,
    two dispatch paths in main.py, two conventions for the same idea. Anything
    that wanted to treat "a thing the assistant can do" uniformly had to know
    which of the two it was holding.

    This module is the single contract both sides now satisfy. A skill declares
    one dict — TOOL, PLUGIN, or SKILL (all three spellings are accepted) — and
    this file normalises it into one `SkillSpec`. One validator, one registry,
    one way to call it, one way to describe it to the model and to the UI.

THE CONTRACT
    A skill file (in actions/, plugins/ or skills/) declares:

        SKILL = {                             # TOOL / PLUGIN also accepted
            "name":        "my_skill",        # ^[a-zA-Z_][a-zA-Z0-9_]{0,63}$
            "description": "...",             # what the model reads to route here
            "parameters":  {"type": "OBJECT", "properties": {...}},
            "handler":     my_function,       # `run` is also accepted
            "requires":    ["requests>=2.31"],# optional pip needs (auto-installed)
            "behavior":    "BLOCKING",        # or NON_BLOCKING
            "scheduling":  "WHEN_IDLE",       # WHEN_IDLE | SILENT | INTERRUPT
            "settings":    {...},             # optional PLUGIN_SETTINGS schema
            "tags":        ["files"],         # optional grouping for the UI
        }

    and a callable that accepts `parameters` plus whichever of the context
    kwargs it declares (player / speak / response / session_memory).

WHY A DATACLASS AND NOT JUST THE DICT
    The dict is what an author writes; the spec is what the app reasons about.
    Having one frozen, validated shape means the loader, the registry, the UI,
    the dependency manager and main.py all read the same fields, and a bad file
    fails in exactly one place with exactly one message.
"""
from __future__ import annotations

import inspect
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

# ── The rules a name must obey ───────────────────────────────────────────────
NAME_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]{0,63}$")

# A skill may spell its declaration block any of these ways. TOOL and PLUGIN are
# the two historical names; SKILL is the one new work should use.
DECL_KEYS = ("SKILL", "TOOL", "PLUGIN")

# The pip-needs block, also accepted under its historical name.
REQUIRES_KEYS = ("requires", "REQUIRES", "dependencies")

BEHAVIORS = ("BLOCKING", "NON_BLOCKING")
SCHEDULING = ("WHEN_IDLE", "SILENT", "INTERRUPT")

# Context kwargs the dispatcher will inject if — and only if — a handler declares
# them. Keeping this list here is what lets one invocation path serve every skill.
CTX_KEYS = ("player", "speak", "response", "session_memory")

DEFAULT_PARAMS = {"type": "OBJECT", "properties": {}}


def _opt_upper(value, allowed: tuple[str, ...]) -> Optional[str]:
    v = str(value or "").strip().upper()
    return v if v in allowed else None


def normalise_requires(raw) -> list[str]:
    """Accept a list, a comma string, or nothing; return a clean list of specs."""
    if not raw:
        return []
    if isinstance(raw, str):
        raw = re.split(r"[,\n]", raw)
    out: list[str] = []
    for item in raw:
        spec = str(item or "").strip()
        if spec and spec not in out:
            out.append(spec)
    return out


@dataclass
class SkillSpec:
    """One validated, callable skill — the single shape the whole app uses."""

    name: str
    description: str = ""
    parameters: dict = field(default_factory=lambda: dict(DEFAULT_PARAMS))
    handler: Optional[Callable] = None
    file: str = ""
    source: str = ""                 # "actions" | "plugins" | "skills"
    valid: bool = False
    error: str = ""
    requires: list[str] = field(default_factory=list)
    settings: Optional[dict] = None
    behavior: Optional[str] = None
    scheduling: Optional[str] = None
    tags: list[str] = field(default_factory=list)

    # ── what the model is told ───────────────────────────────────────────────
    def declaration(self) -> dict:
        decl = {
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters,
        }
        if self.behavior:
            decl["behavior"] = self.behavior
        return decl

    # ── how it is invoked ────────────────────────────────────────────────────
    def call(self, parameters: dict, ctx: dict | None = None) -> str:
        """Run the handler, passing only the context kwargs it declares.

        Never raises: a skill that blows up returns a spoken error instead, so
        one broken tool can never take the session down. main.py still wraps this
        in its own try/except as a second net.
        """
        if not self.valid or self.handler is None:
            return f"Skill '{self.name}' is not available."
        ctx = ctx or {}
        sig = inspect.signature(self.handler)
        has_var_kw = any(p.kind == inspect.Parameter.VAR_KEYWORD
                         for p in sig.parameters.values())
        kwargs = {}
        for key in CTX_KEYS:
            if has_var_kw or key in sig.parameters:
                kwargs[key] = ctx.get(key)
        try:
            return self.handler(parameters=parameters or {}, **kwargs) or "Done."
        except Exception as e:                       # noqa: BLE001 — top-level net
            import traceback
            traceback.print_exc()
            return f"Skill '{self.name}' failed: {e}"

    def to_ui(self) -> dict:
        """The shape the plugin/skill manager overlay renders."""
        return {
            "name": self.name,
            "description": self.description,
            "file": self.file,
            "source": self.source,
            "valid": self.valid,
            "error": self.error,
            "requires": list(self.requires),
            "tags": list(self.tags),
        }


def read_declaration(module) -> tuple[Optional[dict], Optional[str], list[str]]:
    """Pull the declaration dict, its settings schema and its pip needs out of a
    loaded module. Returns (decl, settings, requires); decl is None when the file
    is not a skill at all (a shared helper, for instance)."""
    decl = None
    for key in DECL_KEYS:
        candidate = getattr(module, key, None)
        if isinstance(candidate, dict):
            decl = candidate
            break
    if decl is None:
        return None, None, []

    settings = getattr(module, "PLUGIN_SETTINGS", None)
    if not (isinstance(settings, dict) and isinstance(settings.get("fields"), list)):
        settings = None

    requires = []
    for key in REQUIRES_KEYS:
        if key in decl:
            requires = normalise_requires(decl.get(key))
            break
    if not requires:
        # A module-level REQUIRES is also accepted, for skills that want the
        # dependency list to sit apart from the declaration block.
        requires = normalise_requires(getattr(module, "REQUIRES", None))

    return decl, settings, requires


def validate(module, filename: str, source: str = "") -> SkillSpec:
    """Turn a loaded module into a SkillSpec. Never raises.

    On any problem the returned spec has valid=False and an `error` written for
    the person who wrote or downloaded the file — not a stack trace.
    """
    stem = Path(filename).stem
    decl, settings, requires = read_declaration(module)

    if decl is None:
        return SkillSpec(name=stem, file=filename, source=source,
                         error="No SKILL / TOOL / PLUGIN dict (not a skill file).")

    name = decl.get("name")
    if not isinstance(name, str) or not NAME_RE.match(name):
        return SkillSpec(name=str(name or stem), file=filename, source=source,
                         error="'name' missing or not a valid identifier "
                               "(letters/digits/underscore, must start with a letter).")

    description = decl.get("description")
    if not isinstance(description, str) or not description.strip():
        return SkillSpec(name=name, file=filename, source=source,
                         error="'description' missing or empty.")

    parameters = decl.get("parameters", DEFAULT_PARAMS)
    if not isinstance(parameters, dict) or parameters.get("type") != "OBJECT":
        return SkillSpec(name=name, file=filename, source=source,
                         error="'parameters' must be a dict with \"type\": \"OBJECT\".")

    # `handler` is the new name; `run` is what every existing plugin uses.
    handler = decl.get("handler") or getattr(module, "run", None)
    if not callable(handler):
        return SkillSpec(name=name, file=filename, source=source,
                         error="No callable handler — declare SKILL['handler'] "
                               "or a module-level run(parameters, ...).")

    tags = decl.get("tags")
    if isinstance(tags, str):
        tags = [t.strip() for t in tags.split(",") if t.strip()]
    elif not isinstance(tags, list):
        tags = []

    return SkillSpec(
        name=name,
        description=description.strip(),
        parameters=parameters,
        handler=handler,
        file=filename,
        source=source,
        valid=True,
        requires=requires,
        settings=settings,
        behavior=_opt_upper(decl.get("behavior"), BEHAVIORS),
        scheduling=_opt_upper(decl.get("scheduling"), SCHEDULING),
        tags=[str(t) for t in tags],
    )
