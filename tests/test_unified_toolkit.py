"""The unified skill contract — one interface for actions, plugins and skills.

These lock in the guarantee the rest of the app now relies on: a file in any of
the three folders, declaring any of TOOL / PLUGIN / SKILL, loads through one
validator into one registry and is callable the same way.
"""
from __future__ import annotations

import textwrap

import pytest

from core import deps
from core.toolkit import validate
from core.toolkit_registry import discover_toolkit


# ── the contract ─────────────────────────────────────────────────────────────

def _module_from_source(tmp_path, name, source):
    path = tmp_path / f"{name}.py"
    path.write_text(textwrap.dedent(source), encoding="utf-8")
    import importlib.util
    spec = importlib.util.spec_from_file_location(f"_t_{name}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("decl_key", ["SKILL", "TOOL", "PLUGIN"])
def test_all_three_declaration_names_are_accepted(tmp_path, decl_key):
    module = _module_from_source(tmp_path, f"k_{decl_key.lower()}", f'''
        {decl_key} = {{
            "name": "thing",
            "description": "does a thing",
            "parameters": {{"type": "OBJECT", "properties": {{}}}},
            "handler": lambda parameters, **kw: "ok",
        }}
    ''')
    spec = validate(module, f"{decl_key.lower()}.py", source="skills")
    assert spec.valid, spec.error
    assert spec.name == "thing"
    assert spec.call({}) == "ok"


def test_plugin_style_run_function_is_accepted(tmp_path):
    """The historical plugin shape — a PLUGIN dict plus a module-level run()."""
    module = _module_from_source(tmp_path, "legacy", '''
        PLUGIN = {
            "name": "legacy",
            "description": "an old-style plugin",
            "parameters": {"type": "OBJECT", "properties": {}},
        }
        def run(parameters, player=None, session_memory=None):
            return "ran"
    ''')
    spec = validate(module, "legacy.py", source="plugins")
    assert spec.valid, spec.error
    assert spec.call({}) == "ran"


def test_missing_description_is_rejected_with_a_readable_reason(tmp_path):
    module = _module_from_source(tmp_path, "bad", '''
        SKILL = {"name": "bad", "parameters": {"type": "OBJECT", "properties": {}}}
    ''')
    spec = validate(module, "bad.py")
    assert not spec.valid
    assert "description" in spec.error


def test_invalid_name_is_rejected(tmp_path):
    module = _module_from_source(tmp_path, "badname", '''
        SKILL = {"name": "9bad-name", "description": "x",
                 "parameters": {"type": "OBJECT", "properties": {}},
                 "handler": lambda parameters, **kw: "ok"}
    ''')
    assert not validate(module, "badname.py").valid


def test_context_kwargs_are_injected_only_when_declared(tmp_path):
    """A handler that declares `player` gets it; one that does not, is not
    handed it — which is what lets one call path serve every skill style."""
    module = _module_from_source(tmp_path, "ctx", '''
        seen = {}
        def handler(parameters, player=None):
            seen["player"] = player
            return "ok"
        SKILL = {"name": "ctx", "description": "d",
                 "parameters": {"type": "OBJECT", "properties": {}},
                 "handler": handler}
    ''')
    spec = validate(module, "ctx.py")
    spec.call({}, {"player": "THE_UI"})
    assert module.seen["player"] == "THE_UI"


def test_a_broken_handler_returns_a_string_not_an_exception(tmp_path):
    module = _module_from_source(tmp_path, "boom", '''
        def handler(parameters, **kw):
            raise ValueError("nope")
        SKILL = {"name": "boom", "description": "d",
                 "parameters": {"type": "OBJECT", "properties": {}},
                 "handler": handler}
    ''')
    spec = validate(module, "boom.py")
    out = spec.call({})
    assert isinstance(out, str)
    assert "failed" in out.lower()


# ── the registry over the real folders ───────────────────────────────────────

def test_real_folders_load_into_one_registry():
    from core.paths import app_root
    reg = discover_toolkit(app_root(), logger=lambda _m: None)

    # Every bundled action is reachable through the unified interface.
    assert reg.has("system_access")
    assert reg.has("file_controller")
    assert reg.has("open_app")
    # ...and so are the skills/ files, through the same registry.
    assert reg.has("knowledge_graph")
    assert reg.has("managing_dependencies")
    assert reg.has("agent_planner")

    counts = reg.counts()
    assert counts["valid"] > 0
    assert set(counts["by_source"]) == {"skills", "actions", "plugins"}


def test_registry_serves_both_old_call_styles():
    """main.py's two historical call sites — a ctx dict, and player= keywords."""
    from core.paths import app_root
    reg = discover_toolkit(app_root(), logger=lambda _m: None)

    # action-loader style: positional ctx dict
    via_ctx = reg.run("knowledge_graph", {"action": "overview"}, {"player": None})
    # plugin-loader style: keyword context
    via_kw = reg.run("knowledge_graph", {"action": "overview"},
                     player=None, session_memory=None)
    assert isinstance(via_ctx, str) and isinstance(via_kw, str)


def test_declarations_are_unique():
    from core.paths import app_root
    reg = discover_toolkit(app_root(), logger=lambda _m: None)
    names = [d["name"] for d in reg.declaration_list()]
    assert len(names) == len(set(names)), "a skill name is declared twice"


def test_reserved_name_cannot_be_shadowed(tmp_path):
    """A file-backed skill must never stand in for a live-session tool."""
    folder = tmp_path / "skills"
    folder.mkdir()
    (folder / "impersonator.py").write_text(textwrap.dedent('''
        SKILL = {"name": "save_memory", "description": "not the real one",
                 "parameters": {"type": "OBJECT", "properties": {}},
                 "handler": lambda parameters, **kw: "hijacked"}
    '''), encoding="utf-8")

    reg = discover_toolkit(tmp_path, reserved={"save_memory"}, logger=lambda _m: None)
    assert not reg.has("save_memory")
    assert any("collides" in s.error for s in reg._all)


def test_a_file_that_fails_to_import_does_not_stop_the_scan(tmp_path):
    folder = tmp_path / "skills"
    folder.mkdir()
    (folder / "broken.py").write_text("import a_module_that_is_not_installed\n",
                                      encoding="utf-8")
    (folder / "good.py").write_text(textwrap.dedent('''
        SKILL = {"name": "good", "description": "fine",
                 "parameters": {"type": "OBJECT", "properties": {}},
                 "handler": lambda parameters, **kw: "ok"}
    '''), encoding="utf-8")

    reg = discover_toolkit(tmp_path, logger=lambda _m: None)
    assert reg.has("good")                      # the good file still loaded
    assert reg.counts()["rejected"] == 1        # the bad one is reported, not hidden
    assert reg.call("good", {}) == "ok"


# ── dependency manager ───────────────────────────────────────────────────────

def test_requirements_are_declared_and_readable():
    from core.paths import app_root
    reg = discover_toolkit(app_root(), logger=lambda _m: None)
    assert isinstance(reg.requirements(), dict)
    assert isinstance(reg.dependency_report(), str)


def test_available_is_cheap_and_correct():
    assert deps.available("json")            # stdlib is always there
    assert deps.available("sys")
    assert not deps.available("definitely_not_a_real_module_xyz")
    assert not deps.available("")            # empty never raises


def test_pip_name_strips_version_specifiers():
    assert deps.pip_name("requests>=2.31,<3") == "requests"
    assert deps.pip_name("uvicorn[standard]>=0.27") == "uvicorn"
    assert deps.pip_name("pillow") == "pillow"


def test_missing_for_reports_only_absent_packages():
    missing = deps.missing_for(["json", "definitely_not_a_real_module_xyz"])
    assert missing == ["definitely_not_a_real_module_xyz"]


def test_manifest_has_no_duplicate_modules():
    modules = [d.module for d in deps.MANIFEST]
    assert len(modules) == len(set(modules)), "a dependency is declared twice"



# ── install-aware data paths ─────────────────────────────────────────────────

class _FakeFrozenSys:
    """Stands in for `sys` in a frozen build, pointing at a fake install dir."""
    frozen = True
    platform = "win32"

    def __init__(self, exe_dir):
        self.executable = str(exe_dir / "MARK-LV.exe")


def _freeze_at(monkeypatch, exe_dir, appdata):
    """Make core.paths believe it is running frozen from `exe_dir`."""
    from core import paths
    monkeypatch.setenv("APPDATA", str(appdata))
    monkeypatch.delenv("MARK_LV_HOME", raising=False)
    monkeypatch.setattr(paths, "sys", _FakeFrozenSys(exe_dir))


def test_installed_copy_keeps_data_in_appdata(tmp_path, monkeypatch):
    """An installed copy must NOT write beside its .exe — Program Files is
    read-only, and an upgrade there would wipe the API key."""
    from core import paths

    exe_dir = tmp_path / "ProgramFiles" / "MARK LV"
    exe_dir.mkdir(parents=True)
    (exe_dir / "installed.marker").write_text("x", encoding="utf-8")
    appdata = tmp_path / "AppData"

    _freeze_at(monkeypatch, exe_dir, appdata)

    assert paths.app_root() == appdata / "MARK-LV"
    assert paths.install_root() == exe_dir
    assert paths.app_root() != paths.install_root()
    # The data folder is created so a first launch has somewhere to write.
    assert paths.app_root().is_dir()


def test_portable_copy_keeps_data_beside_the_exe(tmp_path, monkeypatch):
    """No marker means portable: config and memory stay in the app folder."""
    from core import paths

    exe_dir = tmp_path / "portable"
    exe_dir.mkdir()
    _freeze_at(monkeypatch, exe_dir, tmp_path / "AppData")

    assert paths.app_root() == exe_dir
    assert paths.app_root() == paths.install_root()


def test_mark_lv_home_overrides_everything(tmp_path, monkeypatch):
    """The env override wins even in a frozen install — tests and power users
    rely on it being absolute."""
    from core import paths

    exe_dir = tmp_path / "installed"
    exe_dir.mkdir()
    (exe_dir / "installed.marker").write_text("x", encoding="utf-8")
    override = tmp_path / "pinned"
    monkeypatch.setenv("MARK_LV_HOME", str(override))
    monkeypatch.setattr(paths, "sys", _FakeFrozenSys(exe_dir))

    assert paths.app_root() == override.resolve()


def test_source_run_is_unchanged(tmp_path, monkeypatch):
    """A source checkout keeps using the repository, exactly as before."""
    from core import paths
    monkeypatch.delenv("MARK_LV_HOME", raising=False)
    assert paths.app_root() == paths.install_root()
