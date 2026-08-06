"""Tests for Agent Skills discovery, validation, and system prompt injection."""
import pytest

from atlas.application.chat.preprocessors.message_builder import MessageBuilder
from atlas.domain.messages.models import Message, MessageRole
from atlas.domain.sessions.models import Session
from atlas.modules.config import ConfigManager
from atlas.modules.prompts.prompt_provider import PromptProvider
from atlas.modules.skills import SkillRegistry, SkillValidationError, parse_skill_file

VALID_SKILL = """---
name: {name}
description: {description}
---

# Body

Do the thing.
"""


def write_skill(root, name, description="Does a thing. Use when the user asks."):
    """Create a valid skill directory under *root* and return its SKILL.md path."""
    skill_dir = root / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    path = skill_dir / "SKILL.md"
    path.write_text(VALID_SKILL.format(name=name, description=description))
    return path


def make_registry(tmp_path, *roots, enabled=True):
    """Build a registry whose search roots are exactly *roots*, lowest first."""
    config_manager = ConfigManager(atlas_root=tmp_path / "atlas")
    config_manager.app_settings.feature_skills_enabled = enabled
    config_manager.app_settings.skills_paths = ",".join(str(r) for r in roots)
    return SkillRegistry(config_manager)


# --- Frontmatter parsing / spec validation -------------------------------


def test_parse_valid_skill(tmp_path):
    path = write_skill(tmp_path, "pdf-processing", "Extracts text from PDFs.")
    skill = parse_skill_file(path)

    assert skill.name == "pdf-processing"
    assert skill.description == "Extracts text from PDFs."
    assert skill.directory == path.parent


def test_parse_reads_optional_spec_fields(tmp_path):
    skill_dir = tmp_path / "fancy"
    skill_dir.mkdir()
    path = skill_dir / "SKILL.md"
    path.write_text(
        "---\n"
        "name: fancy\n"
        "description: A fancy skill.\n"
        "license: Apache-2.0\n"
        "compatibility: Requires git\n"
        "allowed-tools: Read Bash(git:*)\n"
        "metadata:\n"
        "  author: example-org\n"
        "  version: 1.0\n"
        "---\n\nBody\n"
    )
    skill = parse_skill_file(path)

    assert skill.license == "Apache-2.0"
    assert skill.compatibility == "Requires git"
    assert skill.allowed_tools == "Read Bash(git:*)"
    # YAML turns an unquoted 1.0 into a float; the spec wants string values.
    assert skill.metadata == {"author": "example-org", "version": "1.0"}


@pytest.mark.parametrize(
    "frontmatter",
    [
        "name: mismatch\ndescription: Name does not match the directory.",
        "name: Bad-Name\ndescription: Uppercase is not allowed.",
        "name: -leading\ndescription: Leading hyphen is not allowed.",
        "name: double--hyphen\ndescription: Consecutive hyphens are not allowed.",
        "name: valid-dir\ndescription: ''",
        "name: valid-dir",
        "description: Missing name.",
    ],
)
def test_parse_rejects_invalid_frontmatter(tmp_path, frontmatter):
    skill_dir = tmp_path / "valid-dir"
    skill_dir.mkdir()
    path = skill_dir / "SKILL.md"
    path.write_text(f"---\n{frontmatter}\n---\n\nBody\n")

    with pytest.raises(SkillValidationError):
        parse_skill_file(path)


def test_parse_rejects_missing_frontmatter(tmp_path):
    skill_dir = tmp_path / "no-frontmatter"
    skill_dir.mkdir()
    path = skill_dir / "SKILL.md"
    path.write_text("# Just a markdown file\n")

    with pytest.raises(SkillValidationError):
        parse_skill_file(path)


def test_parse_rejects_overlong_description(tmp_path):
    path = write_skill(tmp_path, "wordy", "x" * 1025)
    with pytest.raises(SkillValidationError):
        parse_skill_file(path)


# --- Discovery -----------------------------------------------------------


def test_discovery_finds_skills_and_sorts_by_name(tmp_path):
    root = tmp_path / "skills"
    write_skill(root, "zebra")
    write_skill(root, "alpha")
    registry = make_registry(tmp_path, root)

    assert [s.name for s in registry.get_skills()] == ["alpha", "zebra"]


def test_later_root_overrides_earlier_by_name(tmp_path):
    packaged = tmp_path / "packaged"
    project = tmp_path / "project"
    write_skill(packaged, "shared", "Packaged version.")
    write_skill(project, "shared", "Project version.")
    write_skill(packaged, "packaged-only", "Only in packaged.")

    registry = make_registry(tmp_path, packaged, project)
    skills = {s.name: s for s in registry.get_skills()}

    # Override by name, but the non-colliding packaged skill survives.
    assert skills["shared"].description == "Project version."
    assert "packaged-only" in skills


def test_invalid_skill_is_skipped_not_fatal(tmp_path):
    root = tmp_path / "skills"
    write_skill(root, "good")
    bad_dir = root / "bad"
    bad_dir.mkdir(parents=True)
    (bad_dir / "SKILL.md").write_text("---\nname: totally-wrong\ndescription: x\n---\n")

    registry = make_registry(tmp_path, root)

    assert [s.name for s in registry.get_skills()] == ["good"]


def test_directories_without_skill_md_are_ignored(tmp_path):
    root = tmp_path / "skills"
    write_skill(root, "real")
    (root / "not-a-skill").mkdir()
    (root / "loose-file.md").write_text("nope")

    registry = make_registry(tmp_path, root)

    assert [s.name for s in registry.get_skills()] == ["real"]


def test_missing_root_is_not_an_error(tmp_path):
    registry = make_registry(tmp_path, tmp_path / "does-not-exist")
    assert registry.get_skills() == []


def test_default_roots_include_claude_and_agents_conventions(tmp_path):
    """Skills authored for other Agent Skills tools are picked up as-is."""
    config_manager = ConfigManager(atlas_root=tmp_path / "atlas")
    config_manager.app_settings.feature_skills_enabled = True
    config_manager.app_settings.skills_paths = ""
    registry = SkillRegistry(config_manager)

    roots = [str(root) for root, _tier in registry.resolve_roots()]
    project_root = str(tmp_path)

    assert f"{tmp_path}/atlas/config/skills" in roots
    assert f"{project_root}/.atlas/skills" in roots
    assert f"{project_root}/.claude/skills" in roots
    assert f"{project_root}/.agents/skills" in roots


# --- Feature flag and cache lifetime -------------------------------------


def test_disabled_feature_yields_no_skills(tmp_path):
    root = tmp_path / "skills"
    write_skill(root, "hidden")
    registry = make_registry(tmp_path, root, enabled=False)

    assert registry.get_skills() == []
    assert registry.render_index() is None


def test_invalidate_picks_up_new_skill_without_restart(tmp_path):
    root = tmp_path / "skills"
    write_skill(root, "first")
    registry = make_registry(tmp_path, root)

    assert [s.name for s in registry.get_skills()] == ["first"]

    write_skill(root, "second")
    # Still cached until explicitly invalidated (which happens per session).
    assert [s.name for s in registry.get_skills()] == ["first"]

    registry.invalidate()
    assert [s.name for s in registry.get_skills()] == ["first", "second"]


@pytest.mark.asyncio
async def test_new_session_invalidates_skill_cache(tmp_path):
    """create_session must rescan so a new skill appears without a restart."""
    from uuid import uuid4

    from atlas.application.chat.service import ChatService

    root = tmp_path / "skills"
    write_skill(root, "first")

    config_manager = ConfigManager(atlas_root=tmp_path / "atlas")
    config_manager.app_settings.feature_skills_enabled = True
    config_manager.app_settings.skills_paths = str(root)

    service = ChatService.__new__(ChatService)
    service.skill_registry = SkillRegistry(config_manager)
    service.session_repository = _StubSessionRepository()

    assert [s.name for s in service.skill_registry.get_skills()] == ["first"]

    write_skill(root, "second")
    await ChatService.create_session(service, session_id=uuid4(), user_email="a@b.c")

    assert [s.name for s in service.skill_registry.get_skills()] == ["first", "second"]


class _StubSessionRepository:
    async def create(self, session):
        return session


# --- Index rendering -----------------------------------------------------


def test_render_index_lists_names_and_descriptions(tmp_path):
    root = tmp_path / "skills"
    write_skill(root, "alpha", "Alpha does A.")
    write_skill(root, "beta", "Beta does B.")
    registry = make_registry(tmp_path, root)

    index = registry.render_index()

    assert "**alpha**: Alpha does A." in index
    assert "**beta**: Beta does B." in index
    # The body must not be inlined — that is the whole point of the index.
    assert "Do the thing." not in index


def test_render_index_is_none_when_no_skills(tmp_path):
    registry = make_registry(tmp_path, tmp_path / "empty")
    assert registry.render_index() is None


# --- System prompt injection ---------------------------------------------


def _session():
    session = Session(user_email="test@example.com")
    session.history.add_message(Message(role=MessageRole.USER, content="Hello"))
    return session


@pytest.mark.asyncio
async def test_index_is_appended_to_default_system_prompt(tmp_path):
    prompts_dir = tmp_path / "prompts"
    prompts_dir.mkdir()
    (prompts_dir / "system_prompt.md").write_text("Base prompt for {user_email}.")

    root = tmp_path / "skills"
    write_skill(root, "alpha", "Alpha does A.")

    config_manager = ConfigManager(atlas_root=tmp_path / "atlas")
    config_manager.app_settings.prompt_base_path = str(prompts_dir)
    config_manager.app_settings.system_prompt_filename = "system_prompt.md"
    config_manager.app_settings.feature_skills_enabled = True
    config_manager.app_settings.skills_paths = str(root)

    builder = MessageBuilder(
        prompt_provider=PromptProvider(config_manager),
        skill_registry=SkillRegistry(config_manager),
    )
    messages = await builder.build_messages(
        session=_session(), include_files_manifest=False, include_system_prompt=True
    )

    system = messages[0]["content"]
    assert messages[0]["role"] == "system"
    assert "Base prompt for test@example.com." in system
    assert "**alpha**: Alpha does A." in system


@pytest.mark.asyncio
async def test_index_is_additive_to_custom_system_prompt(tmp_path):
    """A custom prompt replaces the default prompt but must not drop skills."""
    root = tmp_path / "skills"
    write_skill(root, "alpha", "Alpha does A.")

    config_manager = ConfigManager(atlas_root=tmp_path / "atlas")
    config_manager.app_settings.feature_skills_enabled = True
    config_manager.app_settings.skills_paths = str(root)

    builder = MessageBuilder(skill_registry=SkillRegistry(config_manager))
    messages = await builder.build_messages(
        session=_session(),
        include_files_manifest=False,
        include_system_prompt=True,
        custom_system_prompt="You are a pirate.",
    )

    system = messages[0]["content"]
    assert "You are a pirate." in system
    assert "**alpha**: Alpha does A." in system


@pytest.mark.asyncio
async def test_description_with_braces_does_not_break_formatting(tmp_path):
    """Skill text is appended after str.format, so braces must pass through."""
    prompts_dir = tmp_path / "prompts"
    prompts_dir.mkdir()
    (prompts_dir / "system_prompt.md").write_text("Base for {user_email}.")

    # A brace in a description is legal YAML when quoted, and would raise
    # KeyError if it ever reached str.format() on the system prompt template.
    skill_dir = tmp_path / "skills" / "jsonish"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\n"
        "name: jsonish\n"
        'description: \'Emits {"a": 1} payloads. Use for JSON.\'\n'
        "---\n\nBody\n"
    )
    root = tmp_path / "skills"

    config_manager = ConfigManager(atlas_root=tmp_path / "atlas")
    config_manager.app_settings.prompt_base_path = str(prompts_dir)
    config_manager.app_settings.system_prompt_filename = "system_prompt.md"
    config_manager.app_settings.feature_skills_enabled = True
    config_manager.app_settings.skills_paths = str(root)

    builder = MessageBuilder(
        prompt_provider=PromptProvider(config_manager),
        skill_registry=SkillRegistry(config_manager),
    )
    messages = await builder.build_messages(
        session=_session(), include_files_manifest=False, include_system_prompt=True
    )

    assert '{"a": 1}' in messages[0]["content"]
    assert "Base for test@example.com." in messages[0]["content"]


@pytest.mark.asyncio
async def test_no_injection_when_feature_disabled(tmp_path):
    root = tmp_path / "skills"
    write_skill(root, "alpha", "Alpha does A.")

    config_manager = ConfigManager(atlas_root=tmp_path / "atlas")
    config_manager.app_settings.feature_skills_enabled = False
    config_manager.app_settings.skills_paths = str(root)

    builder = MessageBuilder(skill_registry=SkillRegistry(config_manager))
    messages = await builder.build_messages(
        session=_session(), include_files_manifest=False, include_system_prompt=True
    )

    assert all("alpha" not in m.get("content", "") for m in messages)


# --- Skills MCP server ---------------------------------------------------


@pytest.fixture
def skills_server(tmp_path, monkeypatch):
    """Load the skills MCP server module against a temporary skill root."""
    import importlib.util
    from pathlib import Path

    import atlas

    root = tmp_path / "skills"
    write_skill(root, "alpha", "Alpha does A.")
    (root / "alpha" / "references").mkdir()
    (root / "alpha" / "references" / "REFERENCE.md").write_text("Deep detail.")

    monkeypatch.setenv("FEATURE_SKILLS_ENABLED", "true")
    monkeypatch.setenv("SKILLS_PATHS", str(root))

    server_path = Path(atlas.__file__).parent / "mcp" / "skills" / "main.py"
    spec = importlib.util.spec_from_file_location("skills_server_under_test", server_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    # The module builds its registry from the global config_manager at import
    # time; point it at the temporary root regardless of ambient settings.
    module._registry = make_registry(tmp_path, root)
    return module


def test_server_lists_and_reads_skills(skills_server):
    listing = skills_server.list_skills()
    assert [s["name"] for s in listing["skills"]] == ["alpha"]

    result = skills_server.read_skill("alpha")
    assert "Do the thing." in result["content"]
    assert "references/REFERENCE.md" in result["resources"]


def test_server_reports_unknown_skill(skills_server):
    result = skills_server.read_skill("nope")
    assert "error" in result
    assert result["available_skills"] == ["alpha"]


def test_server_reads_bundled_resource(skills_server):
    result = skills_server.read_skill_resource("alpha", "references/REFERENCE.md")
    assert result["content"] == "Deep detail."


@pytest.mark.parametrize(
    "resource_path",
    ["../../../etc/passwd", "../sibling-skill/SKILL.md", "/etc/passwd"],
)
def test_server_refuses_paths_outside_skill_directory(skills_server, resource_path):
    result = skills_server.read_skill_resource("alpha", resource_path)
    assert "error" in result
    assert "content" not in result


@pytest.mark.asyncio
async def test_packaged_example_skill_is_valid():
    """The skill shipped in atlas/config/skills must satisfy the spec."""
    from pathlib import Path

    import atlas

    packaged = Path(atlas.__file__).parent / "config" / "skills"
    skill_files = sorted(packaged.glob("*/SKILL.md"))

    assert skill_files, "expected at least one packaged skill"
    for path in skill_files:
        parse_skill_file(path)  # raises if invalid
