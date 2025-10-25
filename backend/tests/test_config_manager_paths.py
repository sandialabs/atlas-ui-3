from pathlib import Path

from modules.config.manager import ConfigManager


def test_search_paths_prefer_project_config_dirs():
    cm = ConfigManager()
    paths = cm._search_paths("llmconfig.yml")
    # Ensure both overrides/defaults and legacy paths are present in the list
    str_paths = [str(p) for p in paths]
    assert any("config/overrides" in s for s in str_paths)
    assert any("config/defaults" in s for s in str_paths)
    assert any("backend/configfiles" in s for s in str_paths)
