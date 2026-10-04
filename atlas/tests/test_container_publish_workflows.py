"""Regression checks for multi-platform container publishing."""

from pathlib import Path

import pytest
import yaml


@pytest.fixture(params=[
    ("ci.yml", "production-image", "Build and push production Docker image"),
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
    filename, _, publishing_build = publishing_steps
    if filename != "ci.yml":
        return
    root = Path(__file__).resolve().parents[2]
    workflow = yaml.safe_load(
        (root / ".github" / "workflows" / filename).read_text(encoding="utf-8")
    )
    validation_builds = [
        step
        for job in workflow["jobs"].values()
        for step in job["steps"]
        if step.get("uses", "").startswith("docker/build-push-action@")
        and step is not publishing_build
    ]
    assert len(validation_builds) == 2
    for build in validation_builds:
        inputs = build["with"]
        assert inputs.get("platforms", "linux/amd64") == "linux/amd64"
        assert not inputs.get("push", False)


def test_ci_jobs_run_independently_and_pr_tests_are_not_redundant():
    root = Path(__file__).resolve().parents[2]
    workflow = yaml.safe_load(
        (root / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    )
    jobs = workflow["jobs"]
    assert set(jobs) == {"test", "production-image", "runtime-only-image"}
    assert all("needs" not in job for job in jobs.values())

    test_steps = {step.get("name"): step for step in jobs["test"]["steps"]}
    assert "run_tests.sh all" in test_steps["Run tests in debug mode"]["run"]
    assert "run_tests.sh atlas" in test_steps["Run backend tests in production mode"]["run"]
    assert "run_tests.sh all" not in test_steps["Run backend tests in production mode"]["run"]
    assert test_steps["Run tests in reverse collection order"]["if"] == (
        "github.event_name == 'push' && github.ref == 'refs/heads/main'"
    )

    release_workflow = yaml.safe_load(
        (root / ".github" / "workflows" / "release-weekly.yml").read_text(
            encoding="utf-8"
        )
    )
    checks_step = next(
        step
        for step in release_workflow["jobs"]["release"]["steps"]
        if "REQUIRED_CHECKS" in step.get("env", {})
    )
    required_checks = checks_step["env"]["REQUIRED_CHECKS"].split(",")
    assert {"test", "production-image", "runtime-only-image"} <= set(required_checks)
