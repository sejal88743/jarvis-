"""
_template.py — the ONE way to add a skill to this assistant.

Copy this file, rename it (no leading underscore), fill in SKILL and the
handler. Nothing else needs to change: core/toolkit_registry.py discovers it in
skills/, actions/ or plugins/ on the next launch.

    skills/my_thing.py     <- new work goes here
    actions/my_thing.py    <- bundled skills that drive the computer
    plugins/my_thing.py    <- community downloads, same contract

All three are the same contract now. The only difference is which folder the
file sits in, and that is only used to label it in the UI.
"""
from __future__ import annotations

SKILL = {
    # snake_case, unique across every folder, ^[a-zA-Z_][a-zA-Z0-9_]{0,63}$
    "name": "my_skill",

    # What the model reads to decide when to call this. Say what it does, when
    # to use it, and — if it could be confused with another tool — which tool to
    # use instead. This is the single most important field here.
    "description": (
        "Does the thing. Use this when the user asks for the thing. "
        "Do NOT use this for the other thing — use some_other_skill for that."
    ),

    # The arguments the model fills in. Use uppercase types:
    # STRING | INTEGER | NUMBER | BOOLEAN | ARRAY | OBJECT
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "target": {"type": "STRING", "description": "What to act on"},
        },
        "required": ["target"],
    },

    # The function that runs. It may declare any of these context kwargs and
    # will receive only the ones it declares:
    #     player          the UI — player.write_log("...") to the activity log
    #     speak           speak a sentence mid-task
    #     response        reserved
    #     session_memory  reserved
    "handler": None,          # set below, after the function is defined

    # Pip packages this skill needs. Checked and reported by core/deps.py; the
    # assistant installs them only when the user asks. Use real pip specs.
    "requires": [],

    # Optional. NON_BLOCKING lets the model keep talking while this runs;
    # scheduling decides when its result re-enters the conversation.
    # "behavior":   "BLOCKING",
    # "scheduling": "WHEN_IDLE",     # WHEN_IDLE | SILENT | INTERRUPT

    # Optional grouping for the skill manager UI.
    "tags": ["example"],
}


def my_skill(parameters: dict, player=None, session_memory=None) -> str:
    """Run the skill.

    Return ONE short, natural-language sentence — it is spoken back to the user.
    Never raise: catch your own errors and return a spoken error instead. (The
    registry also catches exceptions as a second net, but do not rely on it.)
    """
    target = (parameters or {}).get("target", "").strip()
    if not target:
        return "I need something to act on."

    try:
        result = f"Did the thing with {target}."
    except Exception as e:
        return f"That failed: {e}"

    if player:
        try:
            player.write_log(f"JARVIS: {result}")
        except Exception:
            pass
    return result


SKILL["handler"] = my_skill
