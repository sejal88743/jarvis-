"""
app_tutor.py — JARVIS learns how YOU use an app, and repeats it next time.

The user teaches a workflow once ("Yahan bill entry aise karte hain: pehle
Payments tab, phir New Entry, bill number khud likhta hai..."), and this skill:

1. save_app_steps  — stores the numbered steps under
   memory/app_steps/<app>.json, together with the date and the user's own
   words. Silently, no announcement.
2. recall_app_steps — before JARVIS drives ANY app it does not know, it calls
   this to check whether the user has taught it before, and follows the saved
   steps exactly.

The store is plain JSON files, one per app, so the user can edit or delete
them by hand. Nothing leaves the machine.
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

BASE    = Path(__file__).resolve().parent.parent
DIR     = BASE / "memory" / "app_steps"

PLUGIN = {
    "name": "app_tutor",
    "description": (
        "Learn and recall HOW the user uses a specific application. "
        "Use save_app_steps whenever the user EXPLAINS a workflow — how to "
        "enter a bill in an app, how to file a report, which tabs to click, in "
        "what order, what goes in which field. Store the steps numbered, "
        "including the app name, and never announce the save. "
        "Use recall_app_steps BEFORE driving an unfamiliar app for the user "
        "(before computer_control / browser_control / any plugin that clicks "
        "around inside an app): if the user has taught this app before, follow "
        "those saved steps exactly instead of guessing."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": "save_app_steps | recall_app_steps | forget_app",
            },
            "app_name": {
                "type": "STRING",
                "description": "The application the steps belong to, e.g. 'HUL Driver Hub', 'Tally', 'WhatsApp'.",
            },
            "steps_text": {
                "type": "STRING",
                "description": "For save_app_steps: the full workflow as numbered steps, in English, extracted from what the user just explained.",
            },
            "notes": {
                "type": "STRING",
                "description": "Optional extra notes: which fields, which buttons, gotchas.",
            },
        },
        "required": ["action", "app_name"],
    },
}

_PLUGIN_SETTINGS = {
    "namespace": "app_tutor",
    "title": "App Tutor",
    "fields": [],
    "action": {"label": "", "run": None},
}


def _slug(app_name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", app_name.strip().lower()).strip("_")
    return s or "unknown_app"


def _path_for(app_name: str) -> Path:
    return DIR / f"{_slug(app_name)}.json"


def _load(app_name: str) -> dict | None:
    p = _path_for(app_name)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def save_app_steps(parameters: dict) -> str:
    app = str(parameters.get("app_name") or "").strip()
    steps = str(parameters.get("steps_text") or "").strip()
    if not app:
        return "App ka naam chahiye — kaunsi app ka tareeka save karna hai?"
    if not steps:
        return "Steps batayein — app kaise chalani hai, kya kya karna hai usme."

    DIR.mkdir(parents=True, exist_ok=True)
    existing = _load(app) or {}
    history = existing.get("history", [])
    history.append({
        "steps": steps,
        "notes": str(parameters.get("notes") or "").strip(),
        "saved_at": time.strftime("%Y-%m-%d %H:%M"),
    })
    data = {
        "app_name": app,
        "steps": steps,           # latest wins; history keeps the older ones
        "notes": str(parameters.get("notes") or "").strip(),
        "updated_at": time.strftime("%Y-%m-%d %H:%M"),
        "history": history[-5:],  # keep the last five teachings
    }
    _path_for(app).write_text(json.dumps(data, indent=2, ensure_ascii=False),
                              encoding="utf-8")
    return f"OK: steps for '{app}' saved."


def recall_app_steps(parameters: dict) -> str:
    app = str(parameters.get("app_name") or "").strip()
    if not app:
        # No app named: list every app we have been taught.
        if not DIR.exists():
            return "NO_STEPS: no app workflows saved yet."
        apps = sorted(p.stem for p in DIR.glob("*.json"))
        if not apps:
            return "NO_STEPS: no app workflows saved yet."
        return "SAVED_APPS: " + ", ".join(apps)

    data = _load(app)
    if not data:
        return f"NO_STEPS: '{app}' ke liye koi saved workflow nahi hai."
    out = (f"SAVED_STEPS for '{data.get('app_name', app)}' "
           f"(updated {data.get('updated_at', '?')}):\n{data.get('steps', '')}")
    if data.get("notes"):
        out += f"\nNotes: {data['notes']}"
    return out


def forget_app(parameters: dict) -> str:
    app = str(parameters.get("app_name") or "").strip()
    p = _path_for(app) if app else None
    if p and p.exists():
        p.unlink()
        return f"OK: '{app}' ke steps bhula diye."
    return f"'{app}' ke liye kuch saved nahi tha."


def run(parameters: dict, player=None, session_memory=None) -> str:
    params = parameters or {}
    action = str(params.get("action") or "").strip().lower()
    try:
        if action == "save_app_steps":
            result = save_app_steps(params)
        elif action == "forget_app":
            result = forget_app(params)
        elif action == "recall_app_steps":
            result = recall_app_steps(params)
        else:
            result = f"app_tutor: unknown action '{action}'"
    except Exception as e:
        result = f"app_tutor '{action}' failed: {e}"
    if player:
        try:
            player.write_log(f"JARVIS: {result}")
        except Exception:
            pass
    return result


_PLUGIN_SETTINGS["action"]["run"] = None
