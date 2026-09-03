"""Tests for the preconfigured persona library (issue #880)."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from atlas.core.compliance import ComplianceLevelManager
from atlas.modules.prompts.persona_library import (
    PersonaLibrary,
    default_search_paths,
    parse_persona_file,
)


def write(tmp_path: Path, name: str, text: str) -> Path:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def test_parses_frontmatter_and_body(tmp_path):
    path = write(tmp_path, "research_assistant.md", """---
name: Research Assistant
description: Careful answers
access_group: research-team
order: 5
---
You are a meticulous research assistant.
""")
    persona = parse_persona_file(path)

    assert persona.id == "research-assistant"
    assert persona.name == "Research Assistant"
    assert persona.description == "Careful answers"
    assert persona.access_groups == ["research-team"]
    assert persona.order == 5
    assert persona.content == "You are a meticulous research assistant."


def test_defaults_come_from_the_filename(tmp_path):
    path = write(tmp_path, "plain-language-editor.md", "Rewrite text clearly.")
    persona = parse_persona_file(path)

    assert persona.id == "plain-language-editor"
    assert persona.name == "Plain Language Editor"
    assert persona.description == ""
    assert persona.access_groups == []
    assert persona.content == "Rewrite text clearly."


def test_access_group_accepts_a_list(tmp_path):
    path = write(tmp_path, "a.md", "---\naccess_group:\n  - alpha\n  - beta\n---\nBody")
    assert parse_persona_file(path).access_groups == ["alpha", "beta"]


def test_broken_frontmatter_skips_the_file(tmp_path):
    # Fail closed: a typo in the metadata must not drop the access_group and
    # publish a gated persona to everyone.
    path = write(tmp_path, "b.md", "---\naccess_group: [unclosed\n---\nStill a prompt.")
    assert parse_persona_file(path) is None


def test_non_mapping_frontmatter_skips_the_file(tmp_path):
    # Same failure mode as broken YAML: the access_group is unreadable.
    path = write(tmp_path, "b.md", "---\n- just\n- a\n- list\n---\nBody")
    assert parse_persona_file(path) is None


def test_empty_body_is_skipped(tmp_path):
    path = write(tmp_path, "c.md", "---\nname: Nothing\n---\n\n   \n")
    assert parse_persona_file(path) is None


def configured_compliance_manager(tmp_path):
    """A ComplianceLevelManager backed by a real config file."""
    cfg = tmp_path / "compliance-levels.json"
    cfg.write_text(json.dumps({
        "levels": [
            {"name": "Public", "allowed_with": ["Public"]},
            {"name": "Internal", "allowed_with": ["Internal"]},
            {"name": "SOC2", "aliases": ["SOC 2"], "allowed_with": ["SOC2"]},
            {"name": "HIPAA", "allowed_with": ["HIPAA", "SOC2"]},
        ]
    }), encoding="utf-8")
    return ComplianceLevelManager(config_path=cfg)


def test_parse_compliance_level_canonicalizes_aliases(tmp_path, monkeypatch):
    from atlas.modules.prompts import persona_library

    manager = configured_compliance_manager(tmp_path)
    monkeypatch.setattr(persona_library, "get_compliance_manager", lambda: manager)

    path = write(tmp_path, "soc.md", "---\ncompliance_level: SOC 2\n---\nBody")
    assert parse_persona_file(path).compliance_level == "SOC2"


def test_parse_invalid_compliance_level_becomes_none(tmp_path, monkeypatch):
    from atlas.modules.prompts import persona_library

    manager = configured_compliance_manager(tmp_path)
    monkeypatch.setattr(persona_library, "get_compliance_manager", lambda: manager)

    path = write(tmp_path, "bad.md", "---\ncompliance_level: NotARealLevel\n---\nBody")
    persona = parse_persona_file(path)

    # The persona still loads (fail open on metadata typos here, unlike the
    # access_group) but carries no level, so strict filtering hides it.
    assert persona is not None
    assert persona.compliance_level is None


def test_parse_without_compliance_level_defaults_to_none(tmp_path):
    path = write(tmp_path, "plain.md", "Just a prompt.")
    assert parse_persona_file(path).compliance_level is None


def test_library_loads_sorted_and_skips_readme(tmp_path):
    write(tmp_path, "README.md", "# Not a persona\n\nDocs.")
    write(tmp_path, "second.md", "---\nname: Second\norder: 20\n---\nB")
    write(tmp_path, "first.md", "---\nname: First\norder: 10\n---\nA")

    personas = PersonaLibrary([tmp_path]).all_personas()

    assert [p.name for p in personas] == ["First", "Second"]


def test_first_directory_with_personas_wins(tmp_path):
    primary = tmp_path / "primary"
    fallback = tmp_path / "fallback"
    primary.mkdir()
    fallback.mkdir()
    write(primary, "custom.md", "Mine")
    write(fallback, "packaged.md", "Theirs")

    personas = PersonaLibrary([primary, fallback]).all_personas()

    assert [p.id for p in personas] == ["custom"]


def test_missing_directories_are_ignored(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    write(real, "x.md", "Body")

    personas = PersonaLibrary([tmp_path / "nope", real]).all_personas()

    assert [p.id for p in personas] == ["x"]


def fake_config_manager(atlas_root: Path, personas_dir: str = "", app_config_dir: str = "config"):
    settings = SimpleNamespace(personas_dir=personas_dir, app_config_dir=app_config_dir)
    return SimpleNamespace(_atlas_root=atlas_root, app_settings=settings)


def test_default_search_paths_prefer_the_config_dir(tmp_path):
    atlas_root = tmp_path / "atlas"
    paths = default_search_paths(fake_config_manager(atlas_root))

    assert paths == [
        tmp_path / "config" / "personas",
        atlas_root / "config" / "prompts" / "personas",
    ]


def test_default_search_paths_personas_dir_setting_wins(tmp_path):
    atlas_root = tmp_path / "atlas"
    paths = default_search_paths(
        fake_config_manager(atlas_root, personas_dir="my/personas")
    )

    assert paths[0] == tmp_path / "my" / "personas"
    assert tmp_path / "config" / "personas" in paths


def test_default_search_paths_absolute_app_config_dir(tmp_path):
    atlas_root = tmp_path / "atlas"
    elsewhere = tmp_path / "elsewhere"
    paths = default_search_paths(
        fake_config_manager(atlas_root, app_config_dir=str(elsewhere))
    )

    assert paths[0] == elsewhere / "personas"


@pytest.mark.asyncio
async def test_personas_for_user_filters_by_group(tmp_path):
    write(tmp_path, "open.md", "Everyone")
    write(tmp_path, "gated.md", "---\naccess_group: secret-team\n---\nMembers only")

    library = PersonaLibrary([tmp_path])

    async def in_no_groups(user, group):
        return False

    async def in_secret_team(user, group):
        return group == "secret-team"

    outsider = await library.personas_for_user("a@b.com", in_no_groups)
    member = await library.personas_for_user("a@b.com", in_secret_team)

    assert [p.id for p in outsider] == ["open"]
    assert sorted(p.id for p in member) == ["gated", "open"]


@pytest.mark.asyncio
async def test_group_check_failure_hides_the_persona(tmp_path):
    write(tmp_path, "gated.md", "---\naccess_group: secret-team\n---\nMembers only")

    async def boom(user, group):
        raise RuntimeError("authorization service down")

    personas = await PersonaLibrary([tmp_path]).personas_for_user("a@b.com", boom)

    assert personas == []


@pytest.mark.asyncio
async def test_each_distinct_group_is_checked_once(tmp_path):
    # Three personas naming two groups between them must cost two authorization
    # calls, not three (and none for the ungated persona).
    write(tmp_path, "open.md", "Everyone")
    write(tmp_path, "a.md", "---\naccess_group: alpha\n---\nA")
    write(tmp_path, "b.md", "---\naccess_group: [alpha, beta]\n---\nB")

    calls = []

    async def counting_check(user, group):
        calls.append(group)
        return True

    personas = await PersonaLibrary([tmp_path]).personas_for_user("a@b.com", counting_check)

    assert sorted(calls) == ["alpha", "beta"]
    assert sorted(p.id for p in personas) == ["a", "b", "open"]


@pytest.mark.asyncio
async def test_persona_for_user_authorizes_only_that_persona(tmp_path):
    write(tmp_path, "open.md", "Everyone")
    write(tmp_path, "gated.md", "---\naccess_group: secret-team\n---\nMembers only")
    library = PersonaLibrary([tmp_path])

    calls = []

    async def counting_check(user, group):
        calls.append(group)
        return group == "secret-team"

    # An ungated persona costs no authorization calls at all.
    assert await library.persona_for_user("open", "a@b.com", counting_check) is not None
    assert calls == []

    assert await library.persona_for_user("gated", "a@b.com", counting_check) is not None
    assert calls == ["secret-team"]

    # Missing and unauthorized are indistinguishable.
    assert await library.persona_for_user("nope", "a@b.com", counting_check) is None

    async def in_no_groups(user, group):
        return False

    assert await library.persona_for_user("gated", "a@b.com", in_no_groups) is None


def test_packaged_personas_parse():
    packaged = Path(__file__).parent.parent / "config" / "prompts" / "personas"
    personas = PersonaLibrary([packaged]).all_personas()

    assert len(personas) >= 3
    assert all(p.content.strip() for p in personas)
    assert "readme" not in [p.id for p in personas]


@pytest.mark.asyncio
async def test_resolve_persona_prompt_authorizes_by_group(tmp_path, monkeypatch):
    """The chat path resolves persona text server-side, re-checking the group."""
    from atlas.modules.prompts import persona_library

    write(tmp_path, "open.md", "Everyone prompt")
    write(tmp_path, "gated.md", "---\naccess_group: secret-team\n---\nGated prompt")

    library = PersonaLibrary([tmp_path])
    monkeypatch.setattr(persona_library, "get_persona_library", lambda: library)

    async def in_no_groups(user, group):
        return False

    resolve = persona_library.resolve_persona_prompt

    assert await resolve("open", "a@b.com", in_no_groups) == "Everyone prompt"
    # Unauthorized, unknown and absent ids all fall back to the default prompt.
    assert await resolve("gated", "a@b.com", in_no_groups) is None
    assert await resolve("nope", "a@b.com", in_no_groups) is None
    assert await resolve(None, "a@b.com", in_no_groups) is None

    async def in_secret_team(user, group):
        return group == "secret-team"

    assert await resolve("gated", "a@b.com", in_secret_team) == "Gated prompt"


@pytest.mark.asyncio
async def test_resolve_persona_prompt_survives_a_broken_library(tmp_path, monkeypatch):
    from atlas.modules.prompts import persona_library

    class Boom:
        async def persona_for_user(self, *args, **kwargs):
            raise RuntimeError("disk on fire")

    monkeypatch.setattr(persona_library, "get_persona_library", lambda: Boom())

    async def group_check(user, group):
        return True

    assert await persona_library.resolve_persona_prompt("x", "a@b.com", group_check) is None


# -- resolve_chat_system_prompt (the /ws chat-turn branch in atlas/main.py) ---


@pytest.mark.asyncio
async def test_chat_system_prompt_inline_wins_over_persona(monkeypatch):
    from atlas.modules.prompts import persona_library

    async def resolve_should_not_run(*args, **kwargs):
        raise AssertionError("persona resolution must not run when inline wins")

    monkeypatch.setattr(persona_library, "resolve_persona_prompt", resolve_should_not_run)

    result = await persona_library.resolve_chat_system_prompt(
        "Inline prompt", "some-persona", "a@b.com", custom_prompts_effective=True
    )

    assert result == "Inline prompt"


@pytest.mark.asyncio
async def test_chat_system_prompt_persona_resolves_with_feature_flag_off(monkeypatch):
    from atlas.modules.prompts import persona_library

    async def fake_resolve(persona_id, user_email, group_check=None, compliance_level_filter=None):
        return "Persona prompt" if persona_id == "ok" else None

    monkeypatch.setattr(persona_library, "resolve_persona_prompt", fake_resolve)

    # Personas are admin-authored: they work with the custom-prompt flag off,
    # and an inline prompt sent while the flag is off is ignored.
    assert await persona_library.resolve_chat_system_prompt(
        None, "ok", "a@b.com", custom_prompts_effective=False
    ) == "Persona prompt"
    assert await persona_library.resolve_chat_system_prompt(
        "Smuggled inline", "ok", "a@b.com", custom_prompts_effective=False
    ) == "Persona prompt"


@pytest.mark.asyncio
async def test_chat_system_prompt_unauthorized_persona_leaves_none(monkeypatch):
    from atlas.modules.prompts import persona_library

    async def fake_resolve(persona_id, user_email, group_check=None, compliance_level_filter=None):
        return None  # unknown or gated away from this user

    monkeypatch.setattr(persona_library, "resolve_persona_prompt", fake_resolve)

    assert await persona_library.resolve_chat_system_prompt(
        None, "gated", "a@b.com", custom_prompts_effective=False
    ) is None
    # Flag off with no persona: an inline prompt is dropped, never applied.
    assert await persona_library.resolve_chat_system_prompt(
        "Smuggled inline", None, "a@b.com", custom_prompts_effective=False
    ) is None


# -- compliance-level enforcement on the persona chat path --------------------


@pytest.fixture
def compliance_setup(tmp_path, monkeypatch):
    """A library with leveled personas plus a configured compliance manager."""
    from atlas.modules.prompts import persona_library

    write(tmp_path, "open.md", "Open prompt")
    write(tmp_path, "internal.md", "---\ncompliance_level: Internal\n---\nInternal prompt")
    write(tmp_path, "soc2.md", "---\ncompliance_level: SOC2\n---\nSOC2 prompt")

    library = PersonaLibrary([tmp_path])
    manager = configured_compliance_manager(tmp_path)
    monkeypatch.setattr(persona_library, "get_persona_library", lambda: library)
    monkeypatch.setattr(persona_library, "get_compliance_manager", lambda: manager)
    monkeypatch.setattr(persona_library, "_compliance_feature_enabled", lambda: True)

    async def in_any_group(user, group):
        return True

    return persona_library, in_any_group


@pytest.mark.asyncio
async def test_compliance_filter_hides_level_less_persona(compliance_setup):
    persona_library, group_check = compliance_setup

    assert await persona_library.resolve_persona_prompt(
        "open", "a@b.com", group_check, compliance_level_filter="Internal"
    ) is None


@pytest.mark.asyncio
async def test_compliance_filter_allows_matching_persona(compliance_setup):
    persona_library, group_check = compliance_setup

    assert await persona_library.resolve_persona_prompt(
        "internal", "a@b.com", group_check, compliance_level_filter="Internal"
    ) == "Internal prompt"


@pytest.mark.asyncio
async def test_compliance_filter_uses_the_allowlist(compliance_setup):
    persona_library, group_check = compliance_setup

    # HIPAA's allowed_with includes SOC2.
    assert await persona_library.resolve_persona_prompt(
        "soc2", "a@b.com", group_check, compliance_level_filter="HIPAA"
    ) == "SOC2 prompt"
    # ...but Internal's does not include SOC2.
    assert await persona_library.resolve_persona_prompt(
        "soc2", "a@b.com", group_check, compliance_level_filter="Internal"
    ) is None


@pytest.mark.asyncio
async def test_no_compliance_filter_allows_everything(compliance_setup):
    persona_library, group_check = compliance_setup

    assert await persona_library.resolve_persona_prompt(
        "open", "a@b.com", group_check, compliance_level_filter=None
    ) == "Open prompt"


@pytest.mark.asyncio
async def test_compliance_not_enforced_when_feature_off(compliance_setup, monkeypatch):
    persona_library, group_check = compliance_setup
    monkeypatch.setattr(persona_library, "_compliance_feature_enabled", lambda: False)

    assert await persona_library.resolve_persona_prompt(
        "open", "a@b.com", group_check, compliance_level_filter="Internal"
    ) == "Open prompt"


@pytest.mark.asyncio
async def test_compliance_filter_flows_through_chat_system_prompt(compliance_setup):
    persona_library, group_check = compliance_setup

    assert await persona_library.resolve_chat_system_prompt(
        None, "internal", "a@b.com",
        custom_prompts_effective=False,
        group_check=group_check,
        compliance_level_filter="Internal",
    ) == "Internal prompt"
    assert await persona_library.resolve_chat_system_prompt(
        None, "open", "a@b.com",
        custom_prompts_effective=False,
        group_check=group_check,
        compliance_level_filter="Internal",
    ) is None
