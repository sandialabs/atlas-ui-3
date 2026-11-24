"""Privacy-safe defaults for published prompt version responses."""

from atlas.modules.prompts.prompt_library_policy import version_view


def sample_version():
    return {
        "id": "version-1",
        "prompt_id": "prompt-1",
        "version_number": 1,
        "title": "Team Prompt",
        "content": "Test prompt content",
        "author_email": "author@example.com",
    }


def test_version_view_omits_author_email_by_default():
    result = version_view(sample_version())
    assert "author_email" not in result
    assert result["content"] == "Test prompt content"


def test_version_view_includes_author_email_when_explicitly_allowed():
    result = version_view(sample_version(), include_author_email=True)
    assert result["author_email"] == "author@example.com"


def test_version_summary_does_not_disclose_author_email():
    result = version_view(sample_version(), include_content=False)
    assert "author_email" not in result
    assert "content" not in result
