import shutil
import zipfile
from pathlib import Path

from setuptools import build_meta


def test_prompt_markdown_files_are_in_built_wheel(tmp_path, monkeypatch):
    repo_root = Path(__file__).resolve().parents[2]
    prompt_files = sorted((repo_root / "prompts").glob("*.md"))
    assert prompt_files

    generated_paths = [repo_root / "atlas_chat.egg-info", repo_root / "build"]
    preexisting_paths = {path for path in generated_paths if path.exists()}

    monkeypatch.chdir(repo_root)
    try:
        wheel_name = build_meta.build_wheel(str(tmp_path))
    finally:
        for path in generated_paths:
            if path.exists() and path not in preexisting_paths:
                shutil.rmtree(path)

    with zipfile.ZipFile(tmp_path / wheel_name) as wheel:
        wheel_paths = set(wheel.namelist())

    for prompt_file in prompt_files:
        assert f"prompts/{prompt_file.name}" in wheel_paths
