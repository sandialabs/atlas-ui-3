"""Tests for the preconfigured persona library (issue #880)."""

from pathlib import Path

import pytest

from atlas.modules.prompts.persona_library import (
    PersonaLibrary,
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


def test_broken_frontmatter_keeps_the_prompt(tmp_path):
    path = write(tmp_path, "b.md", "---\nname: [unclosed\n---\nStill a prompt.")
    persona = parse_persona_file(path)

    assert persona is not None
    assert persona.content == "Still a prompt."
    assert persona.name == "B"


def test_empty_body_is_skipped(tmp_path):
    path = write(tmp_path, "c.md", "---\nname: Nothing\n---\n\n   \n")
    assert parse_persona_file(path) is None


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
        async def personas_for_user(self, *args, **kwargs):
            raise RuntimeError("disk on fire")

    monkeypatch.setattr(persona_library, "get_persona_library", lambda: Boom())

    async def group_check(user, group):
        return True

    assert await persona_library.resolve_persona_prompt("x", "a@b.com", group_check) is None
