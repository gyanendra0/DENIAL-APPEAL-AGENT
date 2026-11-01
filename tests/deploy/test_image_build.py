"""The merge build keeps its promises: both images on both platforms, natively, pushed with the
job's own token, the arm64 image scanned before anything can name it, and every image signed,
with an SBOM, and checked with no login."""

import re
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / ".github" / "workflows"
WORKFLOW = WORKFLOWS / "images.yml"
KUSTOMIZATION = ROOT / "deploy" / "k8s" / "overlays" / "prod" / "kustomization.yaml"


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
    assert trigger["push"]["branches"] == ["main"]
    assert "pull_request" not in trigger


def test_a_merged_deploy_pull_request_starts_no_new_build() -> None:
    ignored = _workflow()[True]["push"]["paths-ignore"]
    assert ignored == ["deploy/k8s/overlays/prod/**"]
    assert KUSTOMIZATION.parent.relative_to(ROOT).as_posix() == ignored[0].removesuffix("/**")


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
    assert _jobs()["publish"]["permissions"] == {"packages": "write", "id-token": "write"}
    assert _jobs()["verify"]["permissions"] == {}
    assert _jobs()["deploy-pr"]["permissions"] == {"contents": "read"}


def test_the_login_uses_the_jobs_token_and_no_stored_secret() -> None:
    assert WORKFLOW.read_text().count("secrets.") == 1  # the deploy-pr App key only
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


def test_cosign_is_checked_against_its_pinned_hash_in_both_jobs_that_use_it() -> None:
    assert re.fullmatch(r"[0-9a-f]{64}", _workflow()["env"]["COSIGN_SHA256"])
    for job in ("publish", "verify"):
        install = _commands(job)[_position(job, "sigstore/cosign/releases")]
        assert "v${COSIGN_VERSION}/cosign-linux-amd64" in install
        assert "sha256sum -c -" in install
        assert _jobs()[job]["runs-on"] == "ubuntu-24.04"


def test_each_platform_gets_its_own_sbom_before_the_login() -> None:
    position = _position("publish", "--format cyclonedx")
    assert position < _position("publish", "docker login ghcr.io")
    sbom = _commands("publish")[position]
    assert "for arch in amd64 arm64" in sbom
    assert 'digests/${arch}")' in sbom


def test_the_image_is_signed_by_digest_after_the_tag_with_each_platform_included() -> None:
    position = _position("publish", 'cosign" sign')
    assert _position("publish", "imagetools create") < position
    sign = _commands("publish")[position]
    assert "--recursive" in sign
    assert '@$(cat "$RUNNER_TEMP/index-digest")' in sign


def test_each_sbom_is_attested_on_the_digest_of_its_own_platform() -> None:
    attest = _commands("publish")[_position("publish", 'cosign" attest')]
    assert "for arch in amd64 arm64" in attest
    assert "--type cyclonedx" in attest
    assert "sbom-${arch}.json" in attest
    assert 'digests/${arch}")' in attest


def test_verify_runs_after_publish_for_both_images_with_no_login() -> None:
    verify = _jobs()["verify"]
    assert verify["needs"] == "publish"
    assert verify["strategy"]["matrix"]["image"] == ["api", "web"]
    assert not any("docker login" in command for command in _commands("verify"))


def test_verify_pins_the_signer_and_covers_the_index_and_each_platform() -> None:
    check = _commands("verify")[_position("verify", 'cosign" verify')]
    assert '--certificate-identity "https://github.com/${GITHUB_WORKFLOW_REF}"' in check
    assert "--certificate-oidc-issuer https://token.actions.githubusercontent.com" in check
    assert '"${repository}@${index}"' in check
    assert check.count('"${repository}@${digest}"') == 2
    assert "verify-attestation --type cyclonedx" in check
    assert "certificate-identity-regexp" not in check


def test_the_deploy_pull_request_comes_only_after_verify_in_the_main_only_environment() -> None:
    deploy = _jobs()["deploy-pr"]
    assert deploy["needs"] == "verify"
    assert deploy["environment"] == "deploy-bot"


def test_the_app_token_is_limited_to_what_the_pull_request_needs() -> None:
    step = next(s for s in _jobs()["deploy-pr"]["steps"] if s.get("id") == "app-token")
    assert step["with"] == {
        "app-id": "${{ vars.DEPLOY_BOT_APP_ID }}",
        "private-key": "${{ secrets.DEPLOY_BOT_PRIVATE_KEY }}",
        "permission-contents": "write",
        "permission-pull-requests": "write",
    }


def test_the_deploy_digests_are_read_from_this_commits_tag_with_no_login() -> None:
    write = _commands("deploy-pr")[_position("deploy-pr", "set_image_digests")]
    assert "for image in api web" in write
    assert ':sha-${GITHUB_SHA}"' in write
    assert not any("docker login" in command for command in _commands("deploy-pr"))


def test_the_workflow_writes_the_file_named_in_the_job() -> None:
    assert (
        _jobs()["deploy-pr"]["env"]["KUSTOMIZATION"] == KUSTOMIZATION.relative_to(ROOT).as_posix()
    )


def test_the_kustomization_names_exactly_the_images_the_build_publishes() -> None:
    images = yaml.safe_load(KUSTOMIZATION.read_text())["images"]
    repository = "ghcr.io/gyanendra0/denial-appeal-agent"
    assert [image["name"] for image in images] == [f"{repository}/api", f"{repository}/web"]
    assert all(re.fullmatch(r"sha256:[0-9a-f]{64}", image["digest"]) for image in images)


def test_no_pull_request_is_opened_when_the_digests_did_not_change() -> None:
    steps = _jobs()["deploy-pr"]["steps"]
    guarded = [s for s in steps if "gh pr" in s.get("run", "")]
    assert len(guarded) == 2
    assert {s["if"] for s in guarded} == {"steps.digests.outputs.changed == 'true'"}


def test_older_deploy_pull_requests_are_closed_before_the_new_one_opens() -> None:
    close = _position("deploy-pr", "gh pr close")
    assert close < _position("deploy-pr", "gh pr create")
    assert 'startswith("deploy/sha-")' in _commands("deploy-pr")[close]


def test_the_deploy_pull_request_uses_the_app_token_and_squash_auto_merge() -> None:
    position = _position("deploy-pr", "gh pr create")
    step = _jobs()["deploy-pr"]["steps"][position]
    assert step["env"] == {
        "GH_TOKEN": "${{ steps.app-token.outputs.token }}",
        "GH_REPO": "${{ github.repository }}",
    }
    assert 'gh pr merge "$url" --auto --squash' in step["run"]
    assert "git push" not in step["run"]


def test_the_trivy_pin_is_the_same_as_in_the_pull_request_scan() -> None:
    security: dict[Any, Any] = yaml.safe_load((WORKFLOWS / "security.yml").read_text())
    for key in ("TRIVY_VERSION", "TRIVY_SHA256"):
        assert _workflow()["env"][key] == security["env"][key]


def test_every_action_is_pinned_to_a_full_commit() -> None:
    actions = [step["uses"] for job in _jobs().values() for step in job["steps"] if "uses" in step]
    assert actions
    assert all(re.fullmatch(r"[\w./-]+@[0-9a-f]{40}", action) for action in actions)


def test_the_checkout_keeps_no_credentials() -> None:
    steps = [
        step
        for job in ("build", "deploy-pr")
        for step in _jobs()[job]["steps"]
        if "checkout" in step.get("uses", "")
    ]
    assert [step["with"]["persist-credentials"] for step in steps] == [False, False]
