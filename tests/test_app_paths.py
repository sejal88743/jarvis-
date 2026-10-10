from pathlib import Path

from core import paths
from memory import config_manager


def test_app_root_can_be_centralized_with_environment_override(monkeypatch, tmp_path):
    root = tmp_path / "portable-jarvis"
    monkeypatch.setenv("MARK_LV_HOME", str(root))

    assert paths.app_root() == root.resolve()
    assert paths.data_path("config", "api_keys.json") == root / "config" / "api_keys.json"


def test_live_screen_share_setting_defaults_off_and_persists(tmp_path, monkeypatch):
    monkeypatch.setattr(config_manager, "CONFIG_DIR", tmp_path / "config")
    monkeypatch.setattr(config_manager, "CONFIG_FILE", tmp_path / "config" / "api_keys.json")

    assert config_manager.get_live_screen_enabled() is False
    config_manager.save_live_screen_enabled(True)
    assert config_manager.get_live_screen_enabled() is True
    config_manager.save_live_screen_enabled(False)
    assert config_manager.get_live_screen_enabled() is False


def test_dashboard_uploads_stay_under_app_root(tmp_path, monkeypatch):
    from dashboard import server

    monkeypatch.setattr(server, "BASE_DIR", tmp_path)

    assert server._make_uploads_dir() == tmp_path / "uploads"
    assert (tmp_path / "uploads").is_dir()


def test_reminder_scripts_stay_under_app_root(tmp_path, monkeypatch):
    from actions import reminder

    monkeypatch.setattr(reminder, "_base_dir", lambda: tmp_path)

    assert reminder._scripts_dir() == tmp_path / "reminders"
    assert (tmp_path / "reminders").is_dir()


def test_browser_fallback_profiles_stay_under_app_root(tmp_path, monkeypatch):
    from actions import browser_control

    monkeypatch.setenv("MARK_LV_HOME", str(tmp_path))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "windows-data"))
    monkeypatch.setenv("APPDATA", str(tmp_path / "roaming-data"))
    monkeypatch.setattr(browser_control, "_OS", "Windows")

    profile = Path(browser_control._real_profile_dir("chrome"))
    assert profile == tmp_path / "data" / "browser_profiles" / "chrome"
    assert profile.is_dir()
