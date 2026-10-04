"""Regression checks for multi-platform container publishing."""

from pathlib import Path

import pytest
import yaml

WORKFLOWS = Path(__file__).resolve().parents[2] / ".github" / "workflows"


def _load(filename):
    return yaml.safe_load((WORKFLOWS / filename).read_text(encoding="utf-8"))


def _build_pushes(workflow):
    return [
        (job_name, step)
        for job_name, job in workflow["jobs"].items()
        for step in job["steps"]
        if step.get("uses", "").startswith("docker/build-push-action@")
    ]


@pytest.fixture(params=[
    ("ci.yml", "publish-image", "Build and push production Docker image"),
    ("quay-publish.yml", "build-and-push-quay", "Build and push to Quay.io"),
])
def publishing_steps(request):
    filename, job, build_name = request.param
    steps = _load(filename)["jobs"][job]["steps"]
    build = next(step for step in steps if step.get("name") == build_name)
    return filename, steps, build


def test_publishing_platforms(publishing_steps):
    filename, _, build = publishing_steps
    inputs = build["with"]
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
    # ci.yml gates the whole publish job on the event, so QEMU needs no own `if`.
    assert "if" not in qemu


def test_pr_production_build_is_validation_only():
    job = _load("ci.yml")["jobs"]["production-image"]
    assert job["if"] == "github.event_name == 'pull_request'"
    builds = [
        step for step in job["steps"]
        if step.get("uses", "").startswith("docker/build-push-action@")
    ]
    assert len(builds) == 1
    inputs = builds[0]["with"]
    assert inputs["platforms"] == "linux/amd64"
    assert not inputs.get("push", False)
    assert not inputs.get("load", False)


def test_branch_publishing_is_gated_on_validation():
    workflow = _load("ci.yml")
    publish = workflow["jobs"]["publish-image"]
    assert publish["if"] == "github.event_name != 'pull_request'"
    assert set(publish["needs"]) == {"test", "runtime-only-image"}
    # Every dependency must run on push, or publishing stops silently while CI
    # stays green.
    for dep in publish["needs"]:
        assert "if" not in workflow["jobs"][dep]
    # Supersede older runs for the same branch/PR so an older commit cannot
    # clobber `latest` after a newer commit has published.
    assert workflow["concurrency"]["group"] == (
        "${{ github.workflow }}-${{ github.ref }}"
    )
    assert workflow["concurrency"]["cancel-in-progress"] is True


def test_pr_and_publish_builds_share_inputs():
    jobs = _load("ci.yml")["jobs"]

    def build(job_name, step_name):
        return next(
            step for step in jobs[job_name]["steps"]
            if step.get("name") == step_name
        )["with"]

    validation = build("production-image", "Build production Docker image")
    publish = build("publish-image", "Build and push production Docker image")
    # Only platforms and push may differ; every other input (context, file,
    # build-args, cache scopes, tags, labels, ...) must match so a PR cannot
    # validate a different image than the one that ships.
    assert set(validation) == set(publish)
    for key in sorted(set(validation) - {"platforms", "push"}):
        assert validation[key] == publish[key], key
    assert validation["platforms"] == "linux/amd64"
    assert validation["push"] is False
    assert publish["platforms"] == "linux/amd64,linux/arm64"
    assert publish["push"] is True

    # The metadata tag lists legitimately differ by event (PR vs branch), but
    # both jobs must build the same image reference and derive labels from the
    # same build metadata, so a drift cannot ship a different image.
    def meta_images(job_name):
        return next(
            step for step in jobs[job_name]["steps"]
            if step.get("name") == "Extract metadata"
        )["with"]["images"]

    def build_meta_run(job_name):
        return next(
            step for step in jobs[job_name]["steps"]
            if step.get("name") == "Get build metadata"
        )["run"]

    assert meta_images("production-image") == meta_images("publish-image") == (
        "${{ env.REGISTRY }}/${{ env.IMAGE_NAME }}"
    )
    assert build_meta_run("production-image") == build_meta_run("publish-image")


def test_production_e2e_runs_on_the_pr_path():
    # The debug-mode suite already runs e2e, but DEBUG_MODE changes auth
    # behaviour, so a production-mode e2e run must also gate PRs (and develop).
    test_job = _load("ci.yml")["jobs"]["test"]
    step = next(
        s for s in test_job["steps"]
        if s.get("name") == "Run e2e tests in production mode"
    )
    assert "if" not in step
    assert "run_tests.sh e2e" in step["run"]


def test_production_steps_run_with_debug_mode_false():
    test_job = _load("ci.yml")["jobs"]["test"]
    for name in (
        "Run backend tests in production mode",
        "Run e2e tests in production mode",
    ):
        step = next(s for s in test_job["steps"] if s.get("name") == name)
        assert "if" not in step, name
        assert "-e DEBUG_MODE=false" in step["run"], name


def test_validation_images_stay_single_platform():
    publication_names = {"Build and push production Docker image"}
    validation_builds = [
        step
        for _, step in _build_pushes(_load("ci.yml"))
        if step.get("name") not in publication_names
    ]
    assert len(validation_builds) == 3
    for build in validation_builds:
        inputs = build["with"]
        assert inputs.get("platforms", "linux/amd64") == "linux/amd64"
        assert not inputs.get("push", False)


def test_ci_jobs_run_independently_and_pr_tests_are_not_redundant():
    jobs = _load("ci.yml")["jobs"]
    assert set(jobs) == {
        "test",
        "production-image",
        "runtime-only-image",
        "publish-image",
    }
    for name in ("test", "production-image", "runtime-only-image"):
        assert "needs" not in jobs[name]

    test_steps = {step.get("name"): step for step in jobs["test"]["steps"]}
    assert "run_tests.sh all" in test_steps["Run tests in debug mode"]["run"]
    assert "run_tests.sh atlas" in test_steps["Run backend tests in production mode"]["run"]
    assert "run_tests.sh all" not in test_steps["Run backend tests in production mode"]["run"]
    assert test_steps["Run tests in reverse collection order"]["if"] == (
        "github.event_name == 'push' && github.ref == 'refs/heads/main'"
    )

    release_workflow = _load("release-weekly.yml")
    checks_step = next(
        step
        for step in release_workflow["jobs"]["release"]["steps"]
        if "REQUIRED_CHECKS" in step.get("env", {})
    )
    required_checks = checks_step["env"]["REQUIRED_CHECKS"].split(",")
    # The release gate must require the renamed PR jobs, and must no longer wait
    # on the retired serial check.
    pr_job_names = {
        step_job.get("name", job_id)
        for job_id, step_job in jobs.items()
        if job_id != "publish-image"
    }
    assert pr_job_names <= set(required_checks)
    assert "build-and-test" not in required_checks
