"""
managing_dependencies.py — the assistant can look after its own dependencies.

WHAT IT DOES
    Answers the two questions a user actually has when a feature will not work:

        "what's missing?"        -> action='report'
        "then install it"        -> action='install'   (or action='install_all')

    and the one a power user has:

        "which skill needs what?" -> action='skills'

WHY IT BELONGS IN skills/
    core/deps.py holds the manifest — the single list of everything this app can
    need. This file is the thin spoken interface over it, so the model can
    diagnose a missing package, say so in the user's language, and fix it when
    asked. Nothing here duplicates the manifest.
"""
from __future__ import annotations

from core import deps

SKILL = {
    "name": "managing_dependencies",
    "description": (
        "Check, report and install the Python packages this assistant needs. "
        "Use action='report' when a feature fails and you suspect a missing "
        "package, or when the user asks what is installed. "
        "Use action='install' with 'package' to install one package, or "
        "action='install_missing' to install everything the base app still "
        "needs. Use action='skills' to list which skills have unmet needs. "
        "Installing changes the machine, so say what you are about to install "
        "before you do it."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": (
                    "report — what is installed and what is missing | "
                    "install — pip-install one package (give 'package') | "
                    "install_missing — install every required package that is absent | "
                    "skills — per-skill dependency status | "
                    "browser — download the Playwright browser for web automation"
                ),
            },
            "package": {
                "type": "STRING",
                "description": "Package name or pip spec for action='install' (e.g. 'pandas' or 'yt-dlp>=2024.1')",
            },
        },
        "required": ["action"],
    },
    "tags": ["system", "maintenance"],
}


def run(parameters: dict, player=None, session_memory=None) -> str:
    p = parameters or {}
    action = str(p.get("action", "")).strip().lower()
    log = (lambda m: player.write_log(m)) if player else None

    if action == "report":
        required = deps.report(include_optional=False)
        everything = deps.report(include_optional=True)
        # `required` already lists the missing mandatory packages; append only
        # the optional ones so the answer is complete without repeating itself.
        optional_missing = [ln for ln in everything.splitlines()
                            if "(optional)" in ln]
        if optional_missing:
            required += ("\n\nOptional extras you could add:\n"
                         + "\n".join(optional_missing))
        return required

    if action == "skills":
        try:
            from core.paths import app_root
            from core.toolkit_registry import discover_toolkit
            from memory.config_manager import get_plugin_enabled
            reg = discover_toolkit(app_root(), logger=lambda _m: None,
                                   enabled_fn=get_plugin_enabled)
            return reg.dependency_report()
        except Exception as e:
            return f"Could not read skill dependencies: {e}"

    if action == "install":
        package = str(p.get("package", "")).strip()
        if not package:
            return "Which package should I install?"
        ok = deps.install(package, log=log)
        return (f"Installed {package}." if ok else
                f"I could not install {package}. Check the activity log — the "
                f"package name may be wrong, or there is no network.")

    if action == "install_missing":
        missing = deps.missing_for(
            [d.package for d in deps.MANIFEST if not d.optional])
        if not missing:
            return "Nothing required is missing — every base dependency is present."
        still = deps.ensure(missing, log=log, auto=True)
        if not still:
            return f"Installed {len(missing)} package(s). Everything required is ready."
        return (f"Installed {len(missing) - len(still)} of {len(missing)}. "
                f"Still missing: {', '.join(still)} — see the activity log.")

    if action == "browser":
        ok = deps.install_playwright_browser(log=log)
        return ("The automation browser is ready." if ok else
                "The browser download failed — check the activity log.")

    return (f"Unknown action '{action}'. Use report, install, install_missing, "
            f"skills or browser.")
