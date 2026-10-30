"""The secret gate keeps its promise: Trivy's default skips stay off in every scan."""

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
