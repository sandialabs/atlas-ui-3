
from atlas.modules.config.config_manager import ConfigManager
from atlas.modules.prompts.prompt_provider import PromptProvider


def test_search_paths_prefer_project_config_dir():
    cm = ConfigManager()
    paths = cm._search_paths("llmconfig.yml")
    str_paths = [str(p) for p in paths]
    # User config dir should be checked first, then package defaults
    assert any("config/llmconfig.yml" in s for s in str_paths)
    assert any("atlas/config/llmconfig.yml" in s for s in str_paths)


def test_prompt_provider_search_paths_prefer_project_config_dir():
    cm = ConfigManager()
    provider = PromptProvider(cm)
    str_paths = [str(p) for p in provider._base_paths]
    assert any("config/prompts" in s for s in str_paths)
    assert any("atlas/config/prompts" in s for s in str_paths)
