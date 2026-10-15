"""Tests for the preconfigured persona library (issue #880)."""

import json
import logging
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


# -- allowed_data_classifications frontmatter (issue #1062) --------------------


def test_parse_allowed_data_classifications_canonicalizes_and_drops_unknown(
    tmp_path, monkeypatch
):
    from atlas.modules.prompts import persona_library

    manager = configured_compliance_manager(tmp_path)
    monkeypatch.setattr(persona_library, "get_compliance_manager", lambda: manager)

    path = write(tmp_path, "multi.md", """---
allowed_data_classifications:
  - SOC 2
  - Internal
  - NotARealLevel
---
Body""")
    persona = parse_persona_file(path)

    # Aliases canonicalize; an unknown level is dropped, never kept.
    assert persona.allowed_data_classifications == ["SOC2", "Internal"]
    # The new field is not a single level, so no legacy level is inferred.
    assert persona.compliance_level is None


def test_legacy_compliance_level_maps_to_one_element_list(tmp_path, monkeypatch):
    from atlas.modules.prompts import persona_library

    manager = configured_compliance_manager(tmp_path)
    monkeypatch.setattr(persona_library, "get_compliance_manager", lambda: manager)

    path = write(tmp_path, "legacy.md", "---\ncompliance_level: SOC 2\n---\nBody")
    persona = parse_persona_file(path)

    assert persona.compliance_level == "SOC2"
    assert persona.allowed_data_classifications == ["SOC2"]


def test_allowed_data_classifications_wins_when_both_are_present(tmp_path, monkeypatch):
    from atlas.modules.prompts import persona_library

    manager = configured_compliance_manager(tmp_path)
    monkeypatch.setattr(persona_library, "get_compliance_manager", lambda: manager)

    path = write(tmp_path, "both.md", """---
compliance_level: HIPAA
allowed_data_classifications:
  - Public
---
Body""")
    persona = parse_persona_file(path)

    assert persona.allowed_data_classifications == ["Public"]
    # The legacy field is ignored for access decisions when the new one shows.
    assert persona.compliance_level is None


def test_explicit_empty_allowed_data_classifications_is_declared_nothing(
    tmp_path, monkeypatch
):
    from atlas.modules.prompts import persona_library

    manager = configured_compliance_manager(tmp_path)
    monkeypatch.setattr(persona_library, "get_compliance_manager", lambda: manager)

    path = write(tmp_path, "empty.md", "---\nallowed_data_classifications: []\n---\nBody")
    persona = parse_persona_file(path)

    # An explicit empty allowlist is not "undeclared": it approves the persona
    # for no classified session.
    assert persona.allowed_data_classifications == []


def test_unreadable_allowed_data_classifications_declares_nothing(tmp_path, monkeypatch):
    from atlas.modules.prompts import persona_library

    manager = configured_compliance_manager(tmp_path)
    monkeypatch.setattr(persona_library, "get_compliance_manager", lambda: manager)

    # A scalar where the list belongs must not be read as a level.
    path = write(tmp_path, "scalar.md", "---\nallowed_data_classifications: 5\n---\nBody")
    assert parse_persona_file(path).allowed_data_classifications == []

    # A present-but-null key also declares nothing rather than falling back to a
    # concurrent legacy level.
    path = write(tmp_path, "null.md", """---
compliance_level: HIPAA
allowed_data_classifications:
---
Body""")
    persona = parse_persona_file(path)
    assert persona.allowed_data_classifications == []
    assert persona.compliance_level is None


def test_string_scalar_allowed_data_classifications_fails_closed(tmp_path, monkeypatch):
    from atlas.modules.prompts import persona_library

    manager = configured_compliance_manager(tmp_path)
    monkeypatch.setattr(persona_library, "get_compliance_manager", lambda: manager)

    # `allowed_data_classifications: Internal` is a common authoring mistake: it
    # must not silently become a one-element allowlist.
    path = write(tmp_path, "scalar.md", "---\nallowed_data_classifications: Internal\n---\nBody")
    assert parse_persona_file(path).allowed_data_classifications == []

    # ...and it must not fall back to a broader legacy level either.
    path = write(tmp_path, "scalar_and_legacy.md", """---
compliance_level: HIPAA
allowed_data_classifications: Internal
---
Body""")
    persona = parse_persona_file(path)
    assert persona.allowed_data_classifications == []
    assert persona.compliance_level is None



def test_all_unknown_allowed_data_classifications_declares_nothing(tmp_path, monkeypatch):
    from atlas.modules.prompts import persona_library

    manager = configured_compliance_manager(tmp_path)
    monkeypatch.setattr(persona_library, "get_compliance_manager", lambda: manager)

    path = write(tmp_path, "typo.md", """---
allowed_data_classifications:
  - NoSuchLevel
---
Body""")
    assert parse_persona_file(path).allowed_data_classifications == []


def test_non_string_allowlist_entries_are_dropped(tmp_path, monkeypatch):
    from atlas.modules.prompts import persona_library

    manager = configured_compliance_manager(tmp_path)
    monkeypatch.setattr(persona_library, "get_compliance_manager", lambda: manager)

    # `- NO` parses as a YAML bool and `- 5` as an int; neither may enter the
    # allowlist, and the real level still does.
    path = write(tmp_path, "mixed.md", """---
allowed_data_classifications:
  - Internal
  - NO
  - 5
---
Body""")
    assert parse_persona_file(path).allowed_data_classifications == ["Internal"]


def test_legacy_compliance_level_as_list_fails_closed(tmp_path, monkeypatch):
    from atlas.modules.prompts import persona_library

    manager = configured_compliance_manager(tmp_path)
    monkeypatch.setattr(persona_library, "get_compliance_manager", lambda: manager)

    # A list under the single-valued field is a mistake: it must not become the
    # repr string "['CUI', 'UUR']" as the persona's level.
    path = write(tmp_path, "listed.md", """---
compliance_level:
  - Internal
  - SOC 2
---
Body""")
    persona = parse_persona_file(path)
    assert persona.compliance_level is None
    assert persona.allowed_data_classifications is None


def test_padded_allowlist_entry_is_stripped(tmp_path, monkeypatch):
    from atlas.modules.prompts import persona_library

    manager = configured_compliance_manager(tmp_path)
    monkeypatch.setattr(persona_library, "get_compliance_manager", lambda: manager)

    path = write(tmp_path, "padded.md", """---
allowed_data_classifications:
  - " Internal "
---
Body""")
    assert parse_persona_file(path).allowed_data_classifications == ["Internal"]


def test_fail_closed_paths_warn(tmp_path, monkeypatch, caplog):
    from atlas.modules.prompts import persona_library

    manager = configured_compliance_manager(tmp_path)
    monkeypatch.setattr(persona_library, "get_compliance_manager", lambda: manager)

    logger = "atlas.modules.prompts.persona_library"
    # A scalar where the list belongs is the admin's only signal that the
    # persona failed closed.
    with caplog.at_level(logging.WARNING, logger=logger):
        parse_persona_file(
            write(tmp_path, "scalar.md", "---\nallowed_data_classifications: Internal\n---\nBody")
        )
    assert "must be a YAML list" in caplog.text

    caplog.clear()
    with caplog.at_level(logging.WARNING, logger=logger):
        parse_persona_file(write(tmp_path, "mixed.md", """---
allowed_data_classifications:
  - Internal
  - NO
---
Body"""))
    assert "ignored" in caplog.text

    caplog.clear()
    with caplog.at_level(logging.WARNING, logger=logger):
        parse_persona_file(write(tmp_path, "listed.md", """---
compliance_level:
  - Internal
---
Body"""))
    assert "allowed_data_classifications" in caplog.text





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
async def test_compliance_filter_requires_the_persona_level(compliance_setup):
    persona_library, group_check = compliance_setup

    # The persona's level must be the active one; HIPAA's allowed_with listing
    # SOC2 no longer widens it (issue #1032).
    assert await persona_library.resolve_persona_prompt(
        "soc2", "a@b.com", group_check, compliance_level_filter="SOC2"
    ) == "SOC2 prompt"
    assert await persona_library.resolve_persona_prompt(
        "soc2", "a@b.com", group_check, compliance_level_filter="HIPAA"
    ) is None
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


# -- allowed_data_classifications enforcement (issue #1062) --------------------


@pytest.fixture
def multi_classification_setup(tmp_path, monkeypatch):
    """A library with an allowlisted persona plus a configured manager."""
    from atlas.modules.prompts import persona_library

    write(tmp_path, "plain.md", "Plain prompt")
    write(tmp_path, "multi.md", """---
allowed_data_classifications:
  - Internal
  - SOC 2
---
Multi prompt""")

    library = PersonaLibrary([tmp_path])
    manager = configured_compliance_manager(tmp_path)
    monkeypatch.setattr(persona_library, "get_persona_library", lambda: library)
    monkeypatch.setattr(persona_library, "get_compliance_manager", lambda: manager)
    monkeypatch.setattr(persona_library, "_compliance_feature_enabled", lambda: True)

    async def in_any_group(user, group):
        return True

    return persona_library, in_any_group


@pytest.mark.asyncio
async def test_multi_classification_persona_visible_at_each_listed_level(
    multi_classification_setup,
):
    persona_library, group_check = multi_classification_setup
    resolve = persona_library.resolve_persona_prompt

    assert await resolve(
        "multi", "a@b.com", group_check, compliance_level_filter="Internal"
    ) == "Multi prompt"
    # An alias of the active level resolves to the same class as the
    # canonical name (the persona lists the aliased "SOC 2").
    assert await resolve(
        "multi", "a@b.com", group_check, compliance_level_filter="SOC 2"
    ) == "Multi prompt"


@pytest.mark.asyncio
async def test_multi_classification_persona_hidden_at_other_levels(
    multi_classification_setup,
):
    persona_library, group_check = multi_classification_setup
    resolve = persona_library.resolve_persona_prompt

    # Public is not in the allowlist, and HIPAA's allowed_with listing SOC2 no
    # longer widens access (issue #1032).
    assert await resolve(
        "multi", "a@b.com", group_check, compliance_level_filter="Public"
    ) is None
    assert await resolve(
        "multi", "a@b.com", group_check, compliance_level_filter="HIPAA"
    ) is None


@pytest.mark.asyncio
async def test_unknown_filter_level_fails_closed(multi_classification_setup):
    persona_library, group_check = multi_classification_setup

    # A filter that names no defined level must not skip the persona check.
    assert await persona_library.resolve_persona_prompt(
        "multi", "a@b.com", group_check, compliance_level_filter="NotARealLevel"
    ) is None
    assert await persona_library.resolve_persona_prompt(
        "plain", "a@b.com", group_check, compliance_level_filter="NotARealLevel"
    ) is None


@pytest.mark.asyncio
async def test_undeclared_persona_hidden_only_under_an_active_filter(
    multi_classification_setup,
):
    persona_library, group_check = multi_classification_setup
    resolve = persona_library.resolve_persona_prompt

    assert await resolve(
        "plain", "a@b.com", group_check, compliance_level_filter="Internal"
    ) is None
    assert await resolve(
        "plain", "a@b.com", group_check, compliance_level_filter=None
    ) == "Plain prompt"
    assert await resolve(
        "multi", "a@b.com", group_check, compliance_level_filter=None
    ) == "Multi prompt"


def test_to_dict_reports_both_legacy_and_effective_classifications(tmp_path, monkeypatch):
    from atlas.modules.prompts import persona_library

    manager = configured_compliance_manager(tmp_path)
    monkeypatch.setattr(persona_library, "get_compliance_manager", lambda: manager)

    multi = parse_persona_file(
        write(tmp_path, "multi.md", """---
allowed_data_classifications:
  - Internal
  - SOC 2
---
Body""")
    )
    multi_dict = multi.to_dict(include_content=False)
    assert multi_dict["allowed_data_classifications"] == ["Internal", "SOC2"]
    assert multi_dict["compliance_level"] is None

    legacy = parse_persona_file(
        write(tmp_path, "legacy.md", "---\ncompliance_level: Internal\n---\nBody")
    )
    legacy_dict = legacy.to_dict(include_content=False)
    assert legacy_dict["allowed_data_classifications"] == ["Internal"]
    assert legacy_dict["compliance_level"] == "Internal"

    # An undeclared persona still carries the key, as an explicit null, so the
    # client's classificationsOf contract is the same for every resource.
    plain = parse_persona_file(write(tmp_path, "plain.md", "Just a prompt."))
    plain_dict = plain.to_dict(include_content=False)
    assert plain_dict["allowed_data_classifications"] is None
    assert "allowed_data_classifications" in plain_dict



# -- startup diagnostics: one clear line per persona per problem -------------


def test_reload_reports_each_unknown_classification_by_name(tmp_path, monkeypatch, caplog):
    from atlas.modules.prompts import persona_library

    manager = configured_compliance_manager(tmp_path)
    monkeypatch.setattr(persona_library, "get_compliance_manager", lambda: manager)

    write(tmp_path, "typo.md", """---
allowed_data_classifications:
  - Internal
  - NoSuchLevel
  - AlsoMissing
---
Body""")

    logger = "atlas.modules.prompts.persona_library"
    with caplog.at_level(logging.WARNING, logger=logger):
        library = PersonaLibrary([tmp_path])
        personas = library.all_personas()

    # The valid entry survives; each bad one is a separate diagnostic that names
    # the offending value and the file, so startup is never silently narrow.
    assert personas[0].allowed_data_classifications == ["Internal"]
    diagnostics = library.diagnostics
    assert [d.source for d in diagnostics] == ["typo.md", "typo.md"]
    assert all(d.level == "warning" for d in diagnostics)
    assert "NoSuchLevel" in diagnostics[0].message
    assert "AlsoMissing" in diagnostics[1].message
    assert diagnostics[0].message.count("typo.md") == 1
    # Each problem is printed once, from the library, as its own log line.
    assert caplog.text.count("NoSuchLevel") == 1
    assert caplog.text.count("AlsoMissing") == 1


def test_reload_reports_every_skipped_file(tmp_path, monkeypatch):
    from atlas.modules.prompts import persona_library

    manager = configured_compliance_manager(tmp_path)
    monkeypatch.setattr(persona_library, "get_compliance_manager", lambda: manager)

    write(tmp_path, "broken.md", "---\naccess_group: [unclosed\n---\nBody")
    write(tmp_path, "empty.md", "---\nname: Nothing\n---\n\n   \n")
    write(tmp_path, "ok.md", "A fine prompt.")

    library = PersonaLibrary([tmp_path])
    personas = library.all_personas()

    assert [p.id for p in personas] == ["ok"]
    by_source = {d.source: d for d in library.diagnostics}
    assert set(by_source) == {"broken.md", "empty.md"}
    assert by_source["broken.md"].level == "error"
    assert "invalid YAML frontmatter" in by_source["broken.md"].message
    assert by_source["empty.md"].level == "warning"
    assert "no prompt text" in by_source["empty.md"].message


def test_clean_library_has_no_diagnostics(tmp_path):
    write(tmp_path, "ok.md", "A fine prompt.")

    library = PersonaLibrary([tmp_path])
    assert library.all_personas()
    assert library.diagnostics == []


def test_reload_logs_a_problem_summary_once(tmp_path, monkeypatch, caplog):
    from atlas.modules.prompts import persona_library

    manager = configured_compliance_manager(tmp_path)
    monkeypatch.setattr(persona_library, "get_compliance_manager", lambda: manager)

    write(tmp_path, "scalar.md", "---\nallowed_data_classifications: Internal\n---\nBody")

    logger = "atlas.modules.prompts.persona_library"
    with caplog.at_level(logging.WARNING, logger=logger):
        PersonaLibrary([tmp_path]).all_personas()

    assert "configuration problem(s) detected" in caplog.text
    assert "allowed_data_classifications must be a YAML list" in caplog.text
    # The warning is emitted by the library, not also by the bare parser call.
    assert caplog.text.count("must be a YAML list") == 1


def test_diagnostics_collapse_embedded_newlines_to_one_line(tmp_path, monkeypatch, caplog):
    from atlas.modules.prompts import persona_library

    manager = configured_compliance_manager(tmp_path)
    monkeypatch.setattr(persona_library, "get_compliance_manager", lambda: manager)

    # A YAML syntax error renders as several context/caret lines; one problem
    # must still be one physical log line.
    write(tmp_path, "broken.md", "---\naccess_group: [unclosed\n---\nBody")

    logger = "atlas.modules.prompts.persona_library"
    with caplog.at_level(logging.WARNING, logger=logger):
        library = PersonaLibrary([tmp_path])
        library.all_personas()

    diagnostic = library.diagnostics[0]
    assert "\n" not in diagnostic.message
    assert "\r" not in diagnostic.message
    # The collapsed line keeps the parser detail, just without the breaks.
    assert "while parsing a flow sequence" in diagnostic.message

    rendered = [r.getMessage() for r in caplog.records if "broken.md" in r.getMessage()]
    assert len(rendered) == 1
    assert all("\n" not in line for line in rendered)


def test_reload_reports_unknown_legacy_compliance_level(tmp_path, monkeypatch):
    from atlas.modules.prompts import persona_library

    manager = configured_compliance_manager(tmp_path)
    monkeypatch.setattr(persona_library, "get_compliance_manager", lambda: manager)

    write(tmp_path, "bad.md", "---\ncompliance_level: NotARealLevel\n---\nBody")

    library = PersonaLibrary([tmp_path])
    personas = library.all_personas()

    # The persona still loads (fail open on the legacy typo) but carries no
    # level, and the diagnostic names the bad value.
    assert personas[0].compliance_level is None
    assert personas[0].allowed_data_classifications is None
    assert [d.level for d in library.diagnostics] == ["warning"]
    assert "NotARealLevel" in library.diagnostics[0].message
    assert "compliance_level" in library.diagnostics[0].message


def test_reload_silent_for_aliased_legacy_compliance_level(tmp_path, monkeypatch):
    from atlas.modules.prompts import persona_library

    manager = configured_compliance_manager(tmp_path)
    monkeypatch.setattr(persona_library, "get_compliance_manager", lambda: manager)

    write(tmp_path, "soc.md", "---\ncompliance_level: SOC 2\n---\nBody")

    library = PersonaLibrary([tmp_path])
    personas = library.all_personas()

    assert personas[0].compliance_level == "SOC2"
    assert personas[0].allowed_data_classifications == ["SOC2"]
    assert library.diagnostics == []


def test_reload_reports_shadowed_duplicate_id(tmp_path):
    write(tmp_path, "a.md", "---\nname: First\norder: 1\n---\nBody")
    write(tmp_path, "b.md", "---\nname: Second\norder: 2\n---\nBody")

    # Both files slugify to id "a"/"b", so force a collision via explicit ids.
    (tmp_path / "b.md").write_text("---\nid: a\nname: Second\n---\nBody", encoding="utf-8")

    library = PersonaLibrary([tmp_path])
    personas = library.all_personas()

    assert [p.id for p in personas] == ["a"]
    shadow = [d for d in library.diagnostics if "shadowed" in d.message]
    assert len(shadow) == 1
    assert shadow[0].source == "b.md"
    assert shadow[0].level == "info"


def test_diagnostics_are_sanitized_against_log_injection(tmp_path, monkeypatch, caplog):
    from atlas.modules.prompts import persona_library

    manager = configured_compliance_manager(tmp_path)
    monkeypatch.setattr(persona_library, "get_compliance_manager", lambda: manager)

    # A level name carrying a control/line-separator sequence must not be able
    # to forge a second log entry.
    write(tmp_path, "inject.md", '---\nallowed_data_classifications:\n  - "Bad\\u2028Level\\nFake"\n---\nBody')

    logger = "atlas.modules.prompts.persona_library"
    with caplog.at_level(logging.WARNING, logger=logger):
        library = PersonaLibrary([tmp_path])
        library.all_personas()

    assert library.diagnostics
    for diagnostic in library.diagnostics:
        assert "\n" not in diagnostic.message
        assert "\u2028" not in diagnostic.message
    assert all("\n" not in r.getMessage() for r in caplog.records)


def test_reload_reports_buffered_diagnostics_even_when_a_file_raises(tmp_path, monkeypatch):
    from atlas.modules.prompts import persona_library

    write(tmp_path, "x.md", "Body")

    def boom(path, diagnostics=None):
        persona_library._report(diagnostics, "warning", path, f"Persona {path.name}: boom")
        raise RuntimeError("kaboom")

    monkeypatch.setattr(persona_library, "parse_persona_file", boom)

    library = persona_library.PersonaLibrary([tmp_path])
    with pytest.raises(RuntimeError):
        library.reload()

    # The buffered diagnostic is reported from the finally block, not lost.
    assert [d.source for d in library.diagnostics] == ["x.md"]


def test_allowlist_canonicalizes_aliases_and_dedupes(tmp_path, monkeypatch):
    from atlas.modules.prompts import persona_library

    manager = configured_compliance_manager(tmp_path)
    monkeypatch.setattr(persona_library, "get_compliance_manager", lambda: manager)

    # An alias listed next to its canonical name, a duplicate, and one unknown:
    # the result is canonical and de-duplicated, with exactly one diagnostic.
    diagnostics = []
    result = persona_library._canonicalize_classifications(
        manager, ["SOC 2", "SOC2", "Internal", "Internal", "NotARealLevel"],
        tmp_path / "p.md", diagnostics,
    )

    assert result == ["SOC2", "Internal"]
    assert len(diagnostics) == 1
    assert "NotARealLevel" in diagnostics[0].message


def test_unknown_entry_is_echoed_with_escapes(tmp_path, monkeypatch):
    from atlas.modules.prompts import persona_library

    manager = configured_compliance_manager(tmp_path)

    diagnostics = []
    result = persona_library._canonicalize_classifications(
        manager, ["Bad\u2028Level\tX"], tmp_path / "p.md", diagnostics,
    )

    assert result == []
    assert len(diagnostics) == 1
    message = diagnostics[0].message
    # Invisible/format characters show as escapes instead of vanishing.
    assert "\\u2028" in message
    assert "\\t" in message
    assert "\u2028" not in message
    assert "\t" not in message


def test_info_only_diagnostics_do_not_log_a_warning_header(tmp_path, caplog):
    (tmp_path / "a.md").write_text("---\nid: dup\nname: First\n---\nBody", encoding="utf-8")
    (tmp_path / "b.md").write_text("---\nid: dup\nname: Second\n---\nBody", encoding="utf-8")

    logger = "atlas.modules.prompts.persona_library"
    with caplog.at_level(logging.INFO, logger=logger):
        library = PersonaLibrary([tmp_path])
        library.all_personas()

    # The shadowed-id notice is info, so no WARNING header announces a problem
    # whose detail line a WARNING threshold would hide.
    assert "configuration problem(s) detected" not in caplog.text
    assert any(
        r.levelno == logging.INFO and "shadowed" in r.getMessage()
        for r in caplog.records
    )
