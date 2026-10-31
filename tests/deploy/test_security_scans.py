"""The Trivy gates keep their promises: the default secret skips stay off in every scan, and
every pull request builds and scans both images."""

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
        gate = next(c for c in commands if '/trivy" image' in c and c.endswith(f"-{image}"))
        assert "--severity HIGH,CRITICAL" in gate
        assert "--ignore-unfixed --exit-code 1" in gate


def test_the_image_scan_pushes_nothing_and_can_only_read() -> None:
    job = yaml.safe_load(WORKFLOW.read_text())["jobs"]["image-scan"]
    assert job["permissions"] == {"contents": "read"}
    assert not any("docker push" in step.get("run", "") for step in job["steps"])
