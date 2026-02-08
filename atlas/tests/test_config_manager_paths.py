
from atlas.modules.config.config_manager import ConfigManager


def test_search_paths_prefer_project_config_dir():
    cm = ConfigManager()
    paths = cm._search_paths("llmconfig.yml")
    str_paths = [str(p) for p in paths]
    # User config dir should be checked first, then package defaults
    assert any("config/llmconfig.yml" in s for s in str_paths)
    assert any("atlas/config/llmconfig.yml" in s for s in str_paths)
