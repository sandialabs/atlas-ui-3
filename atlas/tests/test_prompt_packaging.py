import importlib.resources
import tomllib
from pathlib import Path


def test_prompt_markdown_files_are_package_resources():
    prompt_files = {
        "agent_observe_prompt.md",
        "agent_reason_prompt.md",
        "agent_summary_prompt.md",
        "agent_system_prompt.md",
        "system_prompt.md",
        "tool_synthesis_prompt.md",
    }

    prompts_root = importlib.resources.files("prompts")

    for prompt_file in prompt_files:
        assert (prompts_root / prompt_file).is_file()


def test_pyproject_includes_prompts_package_data():
    pyproject_path = Path(__file__).resolve().parents[2] / "pyproject.toml"
    pyproject = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))

    setuptools_config = pyproject["tool"]["setuptools"]
    assert "prompts*" in setuptools_config["packages"]["find"]["include"]
    assert "*.md" in setuptools_config["package-data"]["prompts"]
