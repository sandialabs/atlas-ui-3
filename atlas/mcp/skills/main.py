#!/usr/bin/env python3
"""Atlas Agent Skills MCP Server.

Serves the *content* half of Agent Skills support. Atlas injects the skill
name/description index into the system prompt; this server lets the model pull
the full instructions for a skill once it decides the skill is relevant, which
is the progressive-disclosure model described in the Agent Skills specification
(https://agentskills.io/specification).

This server only reads files. It never executes anything bundled with a skill,
including files under a skill's ``scripts/`` directory.
"""

from pathlib import Path
from typing import Any, Dict, List

from atlas.mcp_shared.server_factory import create_stdio_server
from atlas.modules.config import config_manager
from atlas.modules.skills import SkillRegistry

mcp = create_stdio_server("Agent Skills")

# Skills are read fresh on every call: this is a long-lived stdio process and
# the on-disk skill set can change underneath it.
_registry = SkillRegistry(config_manager)

# Any file inside a skill's own directory may be read, since skills are free to
# organize their resources however they like. The containment check in
# read_skill_resource is what stops a skill being used to read unrelated files.
MAX_RESOURCE_BYTES = 256 * 1024


def _discover():
    return _registry.discover()


def _find(name: str):
    for skill in _discover():
        if skill.name == name:
            return skill
    return None


@mcp.tool
def list_skills() -> Dict[str, Any]:
    """List all available skills with their names and descriptions.

    The same index is normally already present in the system prompt. Use this
    tool to re-check what is available, or if no skill index was provided.

    Returns:
        A mapping with a "skills" list of {name, description} entries.
    """
    skills = _discover()
    return {
        "skills": [
            {"name": skill.name, "description": skill.description} for skill in skills
        ],
        "count": len(skills),
    }


@mcp.tool
def read_skill(name: str) -> Dict[str, Any]:
    """Read the full instructions for a skill.

    Call this once you have decided a skill applies to the user's request. The
    returned instructions are authoritative; follow them rather than improvising
    from the skill's one-line description.

    Args:
        name: The skill name, exactly as listed in the skill index.

    Returns:
        A mapping with the skill's "content" (the full SKILL.md text) and the
        relative paths of any bundled "resources" that can be read with
        read_skill_resource.
    """
    skill = _find(name)
    if skill is None:
        available = [s.name for s in _discover()]
        return {
            "error": f"No skill named '{name}'.",
            "available_skills": available,
        }

    try:
        content = skill.path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        return {"error": f"Could not read skill '{name}': {e}"}

    resources: List[str] = []
    root = skill.directory
    for path in sorted(root.rglob("*")):
        try:
            if not path.is_file() or path.name == skill.path.name:
                continue
        except OSError:
            continue
        resources.append(str(path.relative_to(root)))

    return {
        "name": skill.name,
        "description": skill.description,
        "content": content,
        "resources": resources,
    }


@mcp.tool
def read_skill_resource(name: str, resource_path: str) -> Dict[str, Any]:
    """Read a file bundled with a skill, such as a reference document.

    Use this for files listed in the "resources" field returned by read_skill,
    for example "references/REFERENCE.md".

    Args:
        name: The skill name.
        resource_path: Path to the file, relative to the skill's directory.

    Returns:
        A mapping with the file's "content", or an "error" describing why it
        could not be read.
    """
    skill = _find(name)
    if skill is None:
        return {"error": f"No skill named '{name}'."}

    # Resolution itself can fail on a broken symlink or an unreadable parent,
    # so keep it inside the guard: an MCP tool must return a structured error
    # rather than raise out of the server process.
    try:
        root = skill.directory.resolve()
        candidate = (root / resource_path).resolve()
    except OSError as e:
        return {"error": f"Could not resolve '{resource_path}': {e}"}

    # Refuse anything that escapes the skill directory, including via symlink
    # or "..", so a skill cannot be used to read unrelated files.
    try:
        candidate.relative_to(root)
    except ValueError:
        return {"error": "resource_path must stay within the skill directory."}

    try:
        if not candidate.is_file():
            return {"error": f"No such file in skill '{name}': {resource_path}"}
        size = candidate.stat().st_size
        if size > MAX_RESOURCE_BYTES:
            return {
                "error": (
                    f"File is {size} bytes, larger than the "
                    f"{MAX_RESOURCE_BYTES} byte limit."
                )
            }
        content = candidate.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        return {"error": f"Could not read '{resource_path}': {e}"}

    return {
        "name": skill.name,
        "resource_path": str(Path(resource_path)),
        "content": content,
    }


if __name__ == "__main__":
    mcp.run(show_banner=False)
