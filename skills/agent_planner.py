"""
agent_planner.py — an autonomous multi-step agent over the skill toolkit.

WHAT THIS ADDS
    Every other skill is one action, one result. That is fine for "open Chrome"
    and useless for "find every PDF in my Downloads, see which ones mention the
    invoice, and zip the matching ones". That request is three tools in a row,
    and the decision to run step two depends on what step one returned.

    This skill is that loop. It:

        1. PLANS   — asks the model to break the request into steps, choosing
                     only from skills that are actually installed *right now*.
        2. RUNS    — executes each step through the real registry, feeding each
                     step's result into the next.
        3. ADAPTS  — if a step fails, re-plans the remainder around the failure
                     instead of stopping.
        4. REPORTS — says what it did, in the user's language, and what it could
                     not do and why.

WHY IT IS SAFE TO RUN UNATTENDED
    The planner cannot invent capabilities: the skill list it is given is built
    from the live registry, so a model that hallucinates `delete_everything`
    gets told the tool does not exist rather than calling it. Every step goes
    through the same undo stack and the same confirmation gate as a direct call.
    Irreversible steps that need a button still need a button.

    A step budget caps runaway plans, and a wall-clock budget caps a plan that
    is technically making progress but will never finish.
"""
from __future__ import annotations

import json
import re
import time

from core import gemini

# How far a single request may go before the agent stops and hands back control.
MAX_STEPS = 8
STEP_BUDGET_S = 240.0

SKILL = {
    "name": "agent_planner",
    "description": (
        "Runs a multi-step task by itself, end to end. Use this when the user's "
        "request needs SEVERAL tools in sequence and the later steps depend on "
        "what the earlier ones return — e.g. 'find all the invoices in my "
        "Downloads and zip them', 'check what is using my CPU and close it', "
        "'organise these files, then open the folder'. "
        "Do NOT use this for a single action — call that tool directly instead. "
        "This is slower than doing one thing, because it plans first."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "task": {
                "type": "STRING",
                "description": "The full goal, in the user's own words",
            },
            "max_steps": {
                "type": "INTEGER",
                "description": f"Step budget (default {MAX_STEPS})",
            },
            "dry_run": {
                "type": "BOOLEAN",
                "description": "Plan only, run nothing — say what you would do",
            },
        },
        "required": ["task"],
    },
    "behavior": "NON_BLOCKING",
    "scheduling": "WHEN_IDLE",
    "tags": ["automation", "agent"],
}


# ── plan shape ───────────────────────────────────────────────────────────────

_PLANNER_SYSTEM = """You are the planning half of a desktop assistant. You do not
act; you produce the shortest correct plan for the tools you are given.

Return ONLY JSON, no markdown, in exactly this shape:
{
  "reasoning": "one sentence on the approach",
  "steps": [
    {"tool": "exact_tool_name", "args": {...}, "why": "what this achieves"}
  ],
  "done_when": "the condition that means the task is complete"
}

Rules that matter more than being thorough:
- Use ONLY tools from the list. A tool that is not listed does not exist. If the
  task cannot be done with the listed tools, return one step with tool "" and
  put the reason in "reasoning".
- Keep the plan SHORT. Three good steps beat eight vague ones. Never pad.
- If the results show the task is already finished or impossible — including a
  normal answer like "not found" — return an EMPTY steps list and say so in
  "reasoning". A "not found" result is a valid ANSWER, not an error.
- Later steps may use the previous step's result: write it as
  "{{prev}}" (a whole value) or "{{prev:field}}" inside a string argument.
- Fill arguments from the user's request. Do not leave placeholder text.
- Order matters: put the step that gathers information before the step that
  acts on it."""


def _tool_catalogue(registry) -> str:
    """The live list of callable skills, for the planner prompt. Built from the
    registry, so the planner can only ever plan with real tools."""
    lines = []
    for spec in sorted(registry._skills.values(), key=lambda s: s.name):
        params = spec.parameters.get("properties", {}) or {}
        args = ", ".join(params.keys()) if params else "no arguments"
        lines.append(f"- {spec.name}({args}): {spec.description[:170]}")
    return "\n".join(lines)


def _parse_json(raw: str) -> dict | None:
    if not raw:
        return None
    text = raw.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text
        text = text.rsplit("```", 1)[0]
    if "{" in text and "}" in text:
        text = text[text.find("{"): text.rfind("}") + 1]
    try:
        return json.loads(text)
    except Exception:
        return None


def _plan(task: str, registry, previous: str = "", failure: str = "") -> dict | None:
    catalogue = _tool_catalogue(registry)
    context = ""
    if previous and not failure:
        context = f"\n\nWhat has happened so far:\n{previous[-2500:]}"
    if failure:
        context = (f"\n\nA step failed:\n{failure[:1200]}\n"
                   f"What has been achieved so far:\n{previous[-1500:]}\n"
                   f"Re-plan the REMAINING work around that failure. Do not "
                   f"repeat a step that already succeeded.")
    prompt = (f"{_PLANNER_SYSTEM}\n\nAvailable tools:\n{catalogue}\n\n"
              f"User's goal: {task}{context}\n\nJSON:")
    resp = gemini.call(prompt, tier=gemini.SMART, timeout_ms=45_000)
    if resp is None:
        return None
    return _parse_json(getattr(resp, "text", "") or "")


# ── argument binding ─────────────────────────────────────────────────────────

def _bind(value, prev_result: str):
    """Resolve {{prev}} / {{prev:field}} references against the last result.

    A reference to a JSON field is looked up if the previous tool returned JSON;
    otherwise the whole text is substituted. Anything unresolvable becomes an
    empty string rather than the literal placeholder — a tool given the string
    "{{prev}}" would do something silly with it.
    """
    if not isinstance(value, str):
        return value
    if "{{prev" not in value:
        return value

    def _sub(match):
        field = (match.group(1) or "").lstrip(":").strip()
        if not field:
            return prev_result
        parsed = _parse_json(prev_result)
        if isinstance(parsed, dict) and field in parsed:
            got = parsed[field]
            return got if isinstance(got, str) else json.dumps(got)
        return ""

    return re.sub(r"\{\{\s*prev\s*(:[^}]*)?\}\}", _sub, value)


def _bind_args(args: dict, prev_result: str) -> dict:
    return {k: _bind(v, prev_result) for k, v in (args or {}).items()}


# ── the loop ─────────────────────────────────────────────────────────────────

def _run_plan(task: str, registry, max_steps: int, dry_run: bool,
              ctx: dict, log) -> str:
    plan = _plan(task, registry)

    if plan is None:
        return ("I could not plan that — the model did not come back with a "
                "usable plan. Try asking for one part of it at a time.")

    steps = plan.get("steps") or []
    if not steps or not steps[0].get("tool"):
        reason = plan.get("reasoning") or "no tool I have can do that."
        return f"I cannot do that with the tools I have installed: {reason}"

    if dry_run:
        lines = [f"Plan for: {task}", ""]
        for i, s in enumerate(steps[:max_steps], 1):
            lines.append(f"{i}. {s.get('tool')} — {s.get('why', '')}")
            if s.get("args"):
                lines.append(f"     args: {json.dumps(s['args'])[:200]}")
        lines.append("")
        lines.append(f"Done when: {plan.get('done_when', 'the goal is met')}")
        return "\n".join(lines)

    log(f"[Agent] plan: {len(steps)} step(s)")
    transcript: list[str] = []
    started = time.monotonic()
    completed = 0
    failure = ""
    replans_used = 0

    for index, step in enumerate(steps[:max_steps], 1):
        if time.monotonic() - started > STEP_BUDGET_S:
            transcript.append(f"Stopped at step {index}: out of time budget.")
            break

        tool = str(step.get("tool", "")).strip()
        args = _bind_args(step.get("args", {}) or {}, transcript[-1] if transcript else "")
        why = step.get("why", "")

        # The model never gets to call something that is not installed.
        if not registry.has(tool):
            failure = f"Step {index} asked for '{tool}', which is not installed."
            log(f"[Agent] {failure}")
            replanned = _plan(task, registry, previous="\n".join(transcript),
                              failure=failure)
            if replanned and replanned.get("steps"):
                steps = steps[:index] + replanned["steps"]
                failure = ""
                continue
            transcript.append(failure)
            break

        if not registry.enabled(tool):
            transcript.append(f"Step {index}: '{tool}' is disabled — skipped.")
            continue

        log(f"[Agent] step {index}: {tool} ({why[:60]})")
        try:
            result = registry.call(tool, args, ctx)
        except Exception as e:                       # registry swallows these too
            result = f"{tool} raised: {e}"

        transcript.append(f"Step {index} — {tool}: {str(result)[:600]}")
        completed += 1

        # A result that reads as a refusal is a failure worth re-planning around.
        # BUT a benign "not found" answer (no such folder, no matching file) is a
        # legitimate final answer, not an error — re-running the plan on it made
        # the agent repeat the whole task and its answer bled into the next
        # user request. Only genuine execution failures re-plan, and at most
        # once per task so one bad step cannot spawn an endless loop.
        lowered = str(result).lower()[:120]
        is_benign_not_found = any(m in lowered for m in ("not found", "does not exist")) \
            and not any(m in lowered for m in ("failed", "error", "raised", "denied"))
        if replans_used < 1 and not is_benign_not_found and any(
               marker in lowered for marker in
               ("failed", "access denied", "could not", "refused",
                "permission denied", "raised")):
            failure = f"Step {index} ({tool}) did not succeed: {str(result)[:400]}"
            replanned = _plan(task, registry, previous="\n".join(transcript),
                              failure=failure)
            if replanned and replanned.get("steps"):
                replans_used += 1
                steps = steps[:index] + replanned["steps"]
                failure = ""
                continue

    log(f"[Agent] finished: {completed} step(s) ran")

    summary = transcript[-1] if transcript else "Nothing ran."
    header = (f"Ran {completed} step(s) for: {task}"
              if completed else f"I could not make progress on: {task}")

    # Compress the transcript into the answer the user hears. The raw step log is
    # returned too, so the model can answer follow-up questions from it.
    spoken = gemini.text(
        f"The assistant just ran a plan for this request: {task}\n\n"
        f"Step results:\n{chr(10).join(transcript)[:3000]}\n\n"
        f"Write ONE or TWO short sentences in the user's own language saying "
        f"what was actually accomplished, and honestly what was not. Report only "
        f"what the results show — invent nothing.",
        tier=gemini.SMART, timeout_ms=30_000, default="")

    if not spoken:
        spoken = summary[:300]

    # Return ONLY the short spoken summary. The full step log used to go back
    # into the conversation too — the live model then read the whole log out
    # loud after already answering, which is the "double reply" the user heard.
    # The log stays on screen via write_log instead.
    return spoken or summary

def _run_plan_full_log(task, transcript, completed, spoken, summary):
    """Kept for tests/debug: the old full-log return shape."""
    header = (f"Ran {completed} step(s) for: {task}"
              if completed else f"I could not make progress on: {task}")
    return f"{header}\n\n{spoken}\n\n--- step log ---\n" + "\n".join(transcript)


def run(parameters: dict, player=None, session_memory=None) -> str:
    p = parameters or {}
    task = str(p.get("task", "")).strip()
    if not task:
        return "What should I do?"

    max_steps = int(p.get("max_steps") or MAX_STEPS)
    max_steps = max(1, min(max_steps, MAX_STEPS))
    dry_run = bool(p.get("dry_run", False))

    log = (lambda m: player.write_log(m)) if player else (lambda _m: None)

    # The live registry — the same object the app dispatches through, so the
    # planner sees exactly what is installed and what is enabled.
    try:
        registry = getattr(player, "_toolkit", None) if player else None
        if registry is None:
            from core.paths import app_root
            from core.toolkit_registry import discover_toolkit
            from memory.config_manager import get_plugin_enabled
            registry = discover_toolkit(app_root(), logger=lambda _m: None,
                                        enabled_fn=get_plugin_enabled)
        # Never let the planner call itself — that is an infinite plan.
        registry._skills.pop("agent_planner", None)
    except Exception as e:
        return f"I could not reach my own tools: {e}"

    ctx = {"player": player, "speak": None, "response": None, "session_memory": session_memory}
    return _run_plan(task, registry, max_steps, dry_run, ctx, log)
