"""The merge build keeps its promises: both images on both platforms, natively, pushed with the
job's own token, and the arm64 image scanned before anything can name it."""

import re
from pathlib import Path
from typing import Any

import yaml

WORKFLOWS = Path(__file__).resolve().parents[2] / ".github" / "workflows"
WORKFLOW = WORKFLOWS / "images.yml"


def _workflow() -> dict[Any, Any]:
    workflow: dict[Any, Any] = yaml.safe_load(WORKFLOW.read_text())
    return workflow


def _jobs() -> dict[str, Any]:
    jobs: dict[str, Any] = _workflow()["jobs"]
    return jobs


def _commands(job: str) -> list[str]:
    return [step.get("run", "") for step in _jobs()[job]["steps"]]


def _position(job: str, text: str) -> int:
    return next(i for i, command in enumerate(_commands(job)) if text in command)


def test_the_build_runs_on_a_merge_and_never_on_a_pull_request() -> None:
    trigger = _workflow()[True]  # YAML reads the key `on` as true.
    assert "main" in trigger["push"]["branches"]
    assert "pull_request" not in trigger


def test_both_images_are_built_on_a_native_runner_of_each_platform() -> None:
    build = _jobs()["build"]
    assert build["strategy"]["matrix"]["image"] == ["api", "web"]
    assert build["strategy"]["matrix"]["platform"] == [
        {"arch": "amd64", "runner": "ubuntu-24.04"},
        {"arch": "arm64", "runner": "ubuntu-24.04-arm"},
    ]
    assert build["runs-on"] == "${{ matrix.platform.runner }}"
    assert not any("setup-qemu" in step.get("uses", "") for step in build["steps"])


def test_every_image_is_published_after_every_build() -> None:
    publish = _jobs()["publish"]
    assert publish["needs"] == "build"
    assert publish["strategy"]["matrix"]["image"] == ["api", "web"]


def test_a_build_pushes_by_digest_and_only_publish_adds_a_tag() -> None:
    push = _commands("build")[_position("build", "docker buildx build")]
    assert "push-by-digest=true" in push
    assert "--tag" not in push
    tag = _commands("publish")[_position("publish", "imagetools create")]
    assert "sha-${GITHUB_SHA}" in tag


def test_the_image_name_is_lowercased_for_the_registry() -> None:
    commands = "\n".join(_commands("build") + _commands("publish"))
    assert "${GITHUB_REPOSITORY,,}" in commands
    assert "${GITHUB_REPOSITORY}" not in commands


def test_only_the_two_jobs_that_push_can_write_packages() -> None:
    assert _workflow()["permissions"] == {}
    assert _jobs()["build"]["permissions"] == {"contents": "read", "packages": "write"}
    assert _jobs()["publish"]["permissions"] == {"packages": "write"}


def test_the_login_uses_the_jobs_token_and_no_stored_secret() -> None:
    assert "secrets." not in WORKFLOW.read_text()
    for job in ("build", "publish"):
        login = _jobs()[job]["steps"][_position(job, "docker login ghcr.io")]
        assert login["env"] == {"GH_TOKEN": "${{ github.token }}"}
        assert "--password-stdin" in login["run"]


def test_a_newer_merge_replaces_an_older_run() -> None:
    assert _workflow()["concurrency"]["cancel-in-progress"] is True


def test_the_arm64_image_is_scanned_before_the_tag_and_before_the_login() -> None:
    scan_position = _position("publish", '/trivy" image')
    assert scan_position < _position("publish", "docker login ghcr.io")
    assert scan_position < _position("publish", "imagetools create")
    scan = _commands("publish")[scan_position]
    assert "--severity HIGH,CRITICAL --ignore-unfixed --exit-code 1" in scan
    assert 'digests/arm64")' in scan


def test_the_trivy_pin_is_the_same_as_in_the_pull_request_scan() -> None:
    security: dict[Any, Any] = yaml.safe_load((WORKFLOWS / "security.yml").read_text())
    for key in ("TRIVY_VERSION", "TRIVY_SHA256"):
        assert _workflow()["env"][key] == security["env"][key]


def test_every_action_is_pinned_to_a_full_commit() -> None:
    actions = [step["uses"] for job in _jobs().values() for step in job["steps"] if "uses" in step]
    assert actions
    assert all(re.fullmatch(r"[\w./-]+@[0-9a-f]{40}", action) for action in actions)


def test_the_checkout_keeps_no_credentials() -> None:
    steps = [step for step in _jobs()["build"]["steps"] if "checkout" in step.get("uses", "")]
    assert [step["with"]["persist-credentials"] for step in steps] == [False]
