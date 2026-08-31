"""Tests for ConfigManager config-file path resolution.

Covers the two-layer lookup (user APP_CONFIG_DIR, then package defaults) and
the repo-root ``config/`` rescue that applies only when the configured user
config directory does not exist on the current machine -- most commonly a
POSIX-style absolute path (``/home/<user>/...``) carried from WSL/Linux into
a Windows ``.env``. On Windows pathlib such a value has no drive letter, is
treated as relative, and joins onto the current drive as ``C:\\home\\...``
(the exact "JSON config not found" symptom with 0 MCP servers loaded).
"""

import logging
from pathlib import PureWindowsPath
from types import SimpleNamespace

import atlas.modules.config.config_loader as config_loader_module
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


def _cm_with_settings(atlas_root, app_config_dir, explicit=True):
    cm = ConfigManager(atlas_root=atlas_root)
    # model_fields_set mimics pydantic-settings: fields explicitly provided
    # from any source. Only app_config_dir is consulted by the loader paths
    # under test.
    cm._app_settings = SimpleNamespace(
        app_config_dir=app_config_dir,
        model_fields_set={"app_config_dir"} if explicit else set(),
    )
    return cm


def test_missing_app_config_dir_falls_back_to_project_config(tmp_path):
    """A nonexistent APP_CONFIG_DIR must not blind the lookup: the conventional
    repo-root config/ is searched (before package defaults) and the file is
    actually loaded from there."""
    atlas_root = tmp_path / "atlas"
    repo_config = tmp_path / "config"
    repo_config.mkdir()
    (repo_config / "mcp.json").write_text('{"calc": {"command": "x"}}', encoding="utf-8")

    cm = _cm_with_settings(atlas_root, "/nonexistent/posix/config")

    paths = cm._search_paths("mcp.json")
    assert repo_config / "mcp.json" in paths
    # Repo-root rescue comes before package defaults.
    assert paths.index(repo_config / "mcp.json") < paths.index(
        atlas_root / "config" / "mcp.json"
    )

    data = cm._load_file_with_error_handling(paths, "JSON")
    assert data == {"calc": {"command": "x"}}


def test_existing_custom_app_config_dir_is_not_padded_from_repo(tmp_path):
    """When APP_CONFIG_DIR points at an existing custom dir, only that dir and
    the package defaults are searched: a deliberately partial custom dir must
    not silently pull in files from the repo-root config/ layer."""
    atlas_root = tmp_path / "atlas"
    custom = tmp_path / "custom"
    custom.mkdir()  # exists, but has no mcp.json
    repo_config = tmp_path / "config"
    repo_config.mkdir()
    (repo_config / "mcp.json").write_text('{"from_repo": {}}', encoding="utf-8")

    cm = _cm_with_settings(atlas_root, str(custom))
    paths = cm._search_paths("mcp.json")
    assert (repo_config / "mcp.json") not in paths
    # Without the file anywhere, the loader degrades to an empty config.
    assert cm._load_file_with_error_handling(paths, "JSON") is None


def test_existing_app_config_dir_wins_over_project_rescue(tmp_path):
    """When APP_CONFIG_DIR is valid, its file wins; the rescue is only reached
    if the configured dir does not exist at all."""
    atlas_root = tmp_path / "atlas"
    custom = tmp_path / "custom"
    custom.mkdir()
    (custom / "mcp.json").write_text('{"from_custom": {}}', encoding="utf-8")
    repo_config = tmp_path / "config"
    repo_config.mkdir()
    (repo_config / "mcp.json").write_text('{"from_repo": {}}', encoding="utf-8")

    cm = _cm_with_settings(atlas_root, str(custom))
    data = cm._load_file_with_error_handling(cm._search_paths("mcp.json"), "JSON")
    assert data == {"from_custom": {}}


def test_default_app_config_dir_deduplicates_project_rescue(tmp_path):
    """With the default relative "config", the rescue candidate is the same
    path as config_dir_project, so it must be deduplicated away."""
    atlas_root = tmp_path / "atlas"
    cm = _cm_with_settings(atlas_root, "config")
    paths = cm._search_paths("mcp.json")
    str_paths = [str(p) for p in paths]
    assert len(str_paths) == len(set(str_paths))


def test_windows_posix_app_config_dir_reproduces_bogus_path_and_recovers(monkeypatch, tmp_path):
    """Reproduce the exact Windows failure with PureWindowsPath (platform-
    independent): APP_CONFIG_DIR=/home/agarlan/git/atlas-ui-3/config has no
    drive under Windows semantics, so the old join produced the logged
    'C:\\home\\agarlan\\git\\atlas-ui-3\\config\\mcp.json'. The repo-root
    rescue must now be present after the bogus candidates so the real
    config/mcp.json is found."""
    atlas_root = PureWindowsPath("C:/Users/agarlan/git/atlas-ui-3/atlas")
    repo_config = PureWindowsPath("C:/Users/agarlan/git/atlas-ui-3/config")

    monkeypatch.setattr(config_loader_module, "Path", PureWindowsPath)
    cm = _cm_with_settings(atlas_root, "/home/agarlan/git/atlas-ui-3/config")

    paths = cm._search_paths("mcp.json")
    str_paths = [str(p) for p in paths]

    # The exact bogus path from the Windows log is still generated (drive-rooted
    # join of the drive-less POSIX value) -- documented, and harmless because it
    # simply does not exist.
    assert "C:\\home\\agarlan\\git\\atlas-ui-3\\config\\mcp.json" in str_paths

    # The fix: the real repo-root config dir is searched before package defaults.
    repo_candidate = str(repo_config / "mcp.json")
    package_defaults = str(atlas_root / "config" / "mcp.json")
    assert repo_candidate in str_paths
    assert package_defaults in str_paths
    assert str_paths.index(repo_candidate) < str_paths.index(package_defaults)


def test_missing_app_config_dir_hint_fires_when_nothing_found(tmp_path, caplog):
    """When no candidate exists, the warning names APP_CONFIG_DIR as the likely
    cause so the operator sees the stale-path problem immediately."""
    atlas_root = tmp_path / "atlas"
    repo_config = tmp_path / "config"
    repo_config.mkdir()  # exists, but has no rag-sources.json

    cm = _cm_with_settings(atlas_root, "/nonexistent/posix/config")
    with caplog.at_level(logging.WARNING, logger="atlas.modules.config.config_loader"):
        data = cm._load_file_with_error_handling(cm._search_paths("rag-sources.json"), "JSON")
    assert data is None
    assert any("APP_CONFIG_DIR" in r.message and "does not exist" in r.message for r in caplog.records)


def test_missing_app_config_dir_hint_skipped_for_pydantic_default(tmp_path, caplog):
    """The hint must not fire when APP_CONFIG_DIR was never explicitly set
    (AppSettings default "config"), e.g. an installed package run from a
    non-repo CWD."""
    atlas_root = tmp_path / "atlas"
    cm = _cm_with_settings(atlas_root, "config", explicit=False)
    with caplog.at_level(logging.WARNING, logger="atlas.modules.config.config_loader"):
        data = cm._load_file_with_error_handling(cm._search_paths("no-such-config.json"), "JSON")
    assert data is None
    assert not any("APP_CONFIG_DIR" in r.message and "does not exist" in r.message for r in caplog.records)
    # The base "not found in any of these locations" warning still fires.
    assert any("not found in any of these locations" in r.message for r in caplog.records)


def test_missing_app_config_dir_hint_not_logged_when_dir_exists(tmp_path, caplog):
    """No extra noise when APP_CONFIG_DIR exists but merely lacks the file."""
    atlas_root = tmp_path / "atlas"
    custom = tmp_path / "custom"
    custom.mkdir()

    cm = _cm_with_settings(atlas_root, str(custom))
    with caplog.at_level(logging.WARNING, logger="atlas.modules.config.config_loader"):
        data = cm._load_file_with_error_handling(cm._search_paths("mcp.json"), "JSON")
    assert data is None
    assert not any("does not exist on this machine" in r.message for r in caplog.records)
    # The base "not found in any of these locations" warning still fires.
    assert any("not found in any of these locations" in r.message for r in caplog.records)
