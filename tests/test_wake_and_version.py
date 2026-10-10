import importlib.util
from pathlib import Path


def _load_setup_module():
    spec = importlib.util.spec_from_file_location("marklv_setup", Path(__file__).resolve().parents[1] / "setup.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_python_3_10_is_supported(monkeypatch):
    module = _load_setup_module()
    monkeypatch.setattr(module.sys, "version_info", (3, 10, 11))

    module._check_python()


def test_python_3_9_is_rejected(monkeypatch):
    module = _load_setup_module()
    monkeypatch.setattr(module.sys, "version_info", (3, 9, 18))

    try:
        module._check_python()
    except SystemExit as exc:
        assert exc.code == 1
    else:
        raise AssertionError("Python 3.9 should be rejected")


def test_wake_word_falls_back_to_normal_listening_when_unavailable(monkeypatch):
    from core import wake_word

    monkeypatch.setattr(wake_word, "is_ready", lambda: False)

    enabled, awake = wake_word.resolve_wake_word_state(True)
    assert enabled is False
    assert awake is True

    enabled, awake = wake_word.resolve_wake_word_state(True, ready=True)
    assert enabled is True
    assert awake is False
