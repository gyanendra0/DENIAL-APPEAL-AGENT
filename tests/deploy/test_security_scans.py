"""The Trivy gates keep their promises: the default secret skips stay off in every scan, every
pull request builds and scans both images, their findings reach the Security tab, and both
Kubernetes overlays are rendered, validated and scanned."""

from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
SECRET_CONFIG = ROOT / "trivy-secret.yaml"
WORKFLOW = ROOT / ".github" / "workflows" / "security.yml"


def _trivy_scans() -> list[str]:
    jobs: dict[str, Any] = yaml.safe_load(WORKFLOW.read_text())["jobs"]
    commands = [step.get("run", "") for step in jobs["trivy"]["steps"]]
    return [command for command in commands if '/trivy" fs' in command]


def _image_scan_steps() -> list[dict[str, Any]]:
    steps: list[dict[str, Any]] = yaml.safe_load(WORKFLOW.read_text())["jobs"]["image-scan"][
        "steps"
    ]
    return steps


def _image_uploads() -> list[dict[str, Any]]:
    return [step for step in _image_scan_steps() if "upload-sarif" in step.get("uses", "")]


def _manifests_job() -> dict[str, Any]:
    job: dict[str, Any] = yaml.safe_load(WORKFLOW.read_text())["jobs"]["manifests"]
    return job


def _manifests_commands() -> list[str]:
    return [step.get("run", "") for step in _manifests_job()["steps"]]


def test_the_secret_gate_also_reads_tests_markdown_and_examples() -> None:
    config = yaml.safe_load(SECRET_CONFIG.read_text())
    assert set(config["disable-allow-rules"]) == {"tests", "markdown", "examples"}


def test_every_scan_for_secrets_uses_the_secret_config() -> None:
    scans = [command for command in _trivy_scans() if "secret" in command]
    assert len(scans) == 2
    assert all("--secret-config trivy-secret.yaml" in command for command in scans)


def test_every_pull_request_builds_and_scans_both_images() -> None:
    workflow = yaml.safe_load(WORKFLOW.read_text())
    assert "pull_request" in workflow[True]  # YAML reads the key `on` as true.
    steps = workflow["jobs"]["image-scan"]["steps"]
    commands = [step.get("run", "") for step in steps]
    for image in ("api", "web"):
        assert f"docker build -f deploy/{image}/Dockerfile -t denial-appeal-agent-{image} ." in (
            commands
        )
        gate = next(
            c
            for c in commands
            if '/trivy" image' in c and "--exit-code" in c and c.endswith(f"-{image}")
        )
        assert "--severity HIGH,CRITICAL" in gate
        assert "--ignore-unfixed --exit-code 1" in gate


def test_the_image_scan_pushes_nothing_and_writes_only_scan_results() -> None:
    job = yaml.safe_load(WORKFLOW.read_text())["jobs"]["image-scan"]
    assert job["permissions"] == {"contents": "read", "security-events": "write"}
    assert not any("docker push" in step.get("run", "") for step in job["steps"])


def test_both_images_reach_the_security_tab_before_any_gate_can_stop_the_job() -> None:
    uploads = _image_uploads()
    assert [step["with"]["category"] for step in uploads] == ["trivy-image-api", "trivy-image-web"]
    steps = _image_scan_steps()
    first_gate = next(i for i, step in enumerate(steps) if "--exit-code" in step.get("run", ""))
    assert all(steps.index(step) < first_gate for step in uploads)


def test_the_image_uploads_skip_dependabot_and_fork_pull_requests() -> None:
    for step in _image_uploads():
        assert "github.event.pull_request.head.repo.full_name == github.repository" in step["if"]
        assert "github.event.pull_request.user.login != 'dependabot[bot]'" in step["if"]


def test_the_image_scans_for_the_security_tab_block_nothing() -> None:
    commands = [step.get("run", "") for step in _image_scan_steps()]
    scans = [command for command in commands if "--format sarif" in command]
    assert len(scans) == 2
    assert not any("--exit-code" in command for command in scans)


def test_every_pull_request_renders_both_overlays_into_the_scanned_folder() -> None:
    assert "pull_request" in yaml.safe_load(WORKFLOW.read_text())[True]
    assert all((ROOT / "deploy" / "k8s" / "overlays" / name).is_dir() for name in ("local", "prod"))
    render = next(c for c in _manifests_commands() if '/kustomize" build' in c)
    assert "for overlay in local prod; do" in render
    assert '"$RUNNER_TEMP/rendered/${overlay}.yaml"' in render


def test_kubeconform_refuses_unknown_fields_and_kinds_for_the_planned_kubernetes() -> None:
    gate = next(c for c in _manifests_commands() if '/kubeconform" ' in c)
    assert "-strict" in gate
    assert "-ignore-missing-schemas" not in gate
    assert "-skip" not in gate
    assert '-kubernetes-version "$KUBERNETES_VERSION"' in gate
    assert gate.endswith('"$RUNNER_TEMP/rendered"')


def test_trivy_blocks_on_high_or_critical_in_the_rendered_manifests() -> None:
    gate = next(c for c in _manifests_commands() if '/trivy" config' in c)
    assert "--severity HIGH,CRITICAL --exit-code 1" in gate
    assert gate.endswith('"$RUNNER_TEMP/rendered"')


def test_the_manifest_gates_come_after_the_render_and_only_read_the_repository() -> None:
    job = _manifests_job()
    assert job["permissions"] == {"contents": "read"}
    assert not any("GH_TOKEN" in step.get("env", {}) for step in job["steps"])
    commands = _manifests_commands()
    render = next(i for i, c in enumerate(commands) if '/kustomize" build' in c)
    gates = [i for i, c in enumerate(commands) if '/kubeconform" ' in c or '/trivy" config' in c]
    assert len(gates) == 2
    assert all(render < gate for gate in gates)


def test_every_manifest_tool_is_pinned_and_its_checksum_verified() -> None:
    job = _manifests_job()
    for tool in ("KUSTOMIZE", "KUBECONFORM"):
        assert len(job["env"][f"{tool}_SHA256"]) == 64
        assert job["env"][f"{tool}_VERSION"]
    installs = [c for c in _manifests_commands() if "curl " in c]
    assert len(installs) == 3
    assert all("sha256sum -c -" in command for command in installs)
