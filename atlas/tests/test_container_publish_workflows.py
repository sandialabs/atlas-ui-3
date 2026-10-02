"""Regression checks for multi-platform container publishing."""

from pathlib import Path

import pytest
import yaml


@pytest.fixture(params=[
    ("ci.yml", "build-and-test", "Build and push production Docker image"),
    ("quay-publish.yml", "build-and-push-quay", "Build and push to Quay.io"),
])
def publishing_steps(request):
    filename, job, build_name = request.param
    root = Path(__file__).resolve().parents[2]
    workflow = yaml.safe_load(
        (root / ".github" / "workflows" / filename).read_text(encoding="utf-8")
    )
    steps = workflow["jobs"][job]["steps"]
    build = next(step for step in steps if step.get("name") == build_name)
    return filename, steps, build


def test_publishing_platforms(publishing_steps):
    filename, _, build = publishing_steps
    inputs = build["with"]
    if filename == "ci.yml":
        assert inputs["platforms"] == (
            "${{ github.event_name == 'pull_request' && 'linux/amd64' "
            "|| 'linux/amd64,linux/arm64' }}"
        )
        assert inputs["push"] == "${{ github.event_name != 'pull_request' }}"
    else:
        assert inputs["platforms"] == "linux/amd64,linux/arm64"
        assert inputs["push"] is True
    assert not inputs.get("load", False)


def test_qemu_precedes_buildx(publishing_steps):
    filename, steps, build = publishing_steps
    qemu = next(
        step for step in steps
        if step.get("uses", "").startswith("docker/setup-qemu-action@")
    )
    buildx = next(
        step for step in steps
        if step.get("uses", "").startswith("docker/setup-buildx-action@")
    )
    assert steps.index(qemu) < steps.index(buildx) < steps.index(build)
    assert qemu["with"]["platforms"] == "arm64"
    if filename == "ci.yml":
        assert qemu["if"] == "github.event_name != 'pull_request'"
    else:
        assert "if" not in qemu


def test_validation_images_stay_single_platform(publishing_steps):
    filename, steps, publishing_build = publishing_steps
    if filename != "ci.yml":
        return
    validation_builds = [
        step for step in steps
        if step.get("uses", "").startswith("docker/build-push-action@")
        and step is not publishing_build
    ]
    assert len(validation_builds) == 2
    for build in validation_builds:
        inputs = build["with"]
        assert inputs.get("platforms", "linux/amd64") == "linux/amd64"
        assert not inputs.get("push", False)
