"""A backup can be restored: the restore script only ever creates a new database, and the CI job
`backup-restore` runs the CronJob's own backup.sh the way the CronJob runs it, restores the dump
into a second server and finds every table with the same row count."""

import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
BACKUP = ROOT / "deploy" / "k8s" / "base" / "backup"
RESTORE = BACKUP / "restore.sh"
ROW_COUNTS = BACKUP / "row-counts.sql"
FIXTURE = Path(__file__).parent / "fixtures" / "backup-rows.sql"
WORKFLOW = ROOT / ".github" / "workflows" / "checks.yml"
COMPOSE_FILE = ROOT / "deploy" / "prod" / "compose.yml"


def _workflow() -> dict[Any, Any]:
    workflow: dict[Any, Any] = yaml.safe_load(WORKFLOW.read_text())
    return workflow


def _job() -> dict[str, Any]:
    job: dict[str, Any] = _workflow()["jobs"]["backup-restore"]
    return job


def _commands() -> list[str]:
    return [step.get("run", "") for step in _job()["steps"]]


def _index_of(text: str) -> int:
    return next(i for i, command in enumerate(_commands()) if text in command)


def _bash() -> str:
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("needs bash")
    return bash


def test_the_restore_script_is_valid_bash_that_stops_on_any_failure() -> None:
    text = RESTORE.read_text()
    assert text.startswith("#!/bin/bash\n")
    assert "set -euo pipefail" in text
    # Only reads our own committed file; nothing from outside reaches the command.
    result = subprocess.run(  # noqa: S603
        [_bash(), "-n", str(RESTORE)], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr


def test_the_restore_only_creates_a_new_database() -> None:
    text = RESTORE.read_text()
    assert 'createdb "$database"' in text
    for overwrite in ("DROP", "dropdb", "--clean", "--create"):
        assert overwrite not in text


@pytest.mark.parametrize("name", ["", "App", "app;drop", "-d", "1app", "postgresql://x/app"])
def test_the_restore_refuses_a_database_name_that_is_not_a_plain_name(name: str) -> None:
    # Refused before gpg, createdb or the network is touched, so the made-up paths are never read.
    result = subprocess.run(  # noqa: S603
        [_bash(), str(RESTORE), "made-up.dump.gpg", "made-up.asc", name],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 2
    assert "refused" in result.stderr


def test_the_restore_decrypts_through_a_pipe_in_one_transaction() -> None:
    text = RESTORE.read_text()
    assert 'gpg --batch --quiet --import "$private_key"' in text
    assert 'GNUPGHOME="$(mktemp -d)"' in text
    assert "--pinentry-mode loopback --decrypt" in text
    assert "| pg_restore --no-owner --single-transaction --exit-on-error" in text
    assert "--output" not in text


def test_the_restore_script_never_reaches_the_server() -> None:
    kustomization = yaml.safe_load((BACKUP.parent / "kustomization.yaml").read_text())
    files = [f for generator in kustomization["configMapGenerator"] for f in generator["files"]]
    assert "backup/restore.sh" not in files
    assert "backup/row-counts.sql" not in files


def test_the_row_counts_cover_every_table_exactly() -> None:
    text = ROW_COUNTS.read_text()
    assert "table_schema = 'public' AND table_type = 'BASE TABLE'" in text
    assert "SELECT count(*)" in text
    assert "reltuples" not in text and "n_live_tup" not in text


def test_the_fixture_covers_the_types_a_restore_could_get_wrong() -> None:
    text = FIXTURE.read_text()
    assert "CREATE EXTENSION IF NOT EXISTS vector" in text
    for column_type in ("vector(3)", "numeric(", "jsonb", "timestamptz"):
        assert column_type in text
    assert len(re.findall(r"^CREATE TABLE", text, re.MULTILINE)) >= 3


def test_every_pull_request_runs_the_restore_test_with_no_secret() -> None:
    # PyYAML reads the bare key `on` as True.
    assert "pull_request" in _workflow()[True]
    job = _job()
    assert job["permissions"] == {"contents": "read"}
    assert "environment" not in job
    assert "secrets." not in WORKFLOW.read_text()
    assert job["steps"][0]["with"]["persist-credentials"] is False


def test_the_restore_test_uses_the_database_image_of_the_server() -> None:
    compose = yaml.safe_load(COMPOSE_FILE.read_text())
    assert _job()["env"]["DB_IMAGE"] == compose["services"]["db"]["image"]
    for command in _commands():
        if "docker run" in command:
            assert '"$DB_IMAGE"' in command


def test_the_backup_runs_the_cronjobs_own_script_as_the_cronjob_runs_it() -> None:
    backup = _commands()[_index_of("bash /scripts/backup.sh")]
    assert '"$PWD/deploy/k8s/base/backup:/scripts:ro"' in backup
    assert "--user 999:999 --read-only --tmpfs /tmp" in backup
    cronjob = next(
        d
        for d in yaml.safe_load_all((BACKUP.parent / "backup.yaml").read_text())
        if d["kind"] == "CronJob"
    )
    pod = cronjob["spec"]["jobTemplate"]["spec"]["template"]["spec"]
    assert pod["securityContext"]["runAsUser"] == 999
    assert pod["initContainers"][0]["command"][:2] == ["bash", "/scripts/backup.sh"]


def test_the_key_is_made_fresh_with_no_network() -> None:
    keys = _commands()[_index_of("--quick-gen-key")]
    assert "--network none" in keys
    assert "backup-restore-ci" in keys


def test_backup_then_restore_then_counts_then_the_refusal() -> None:
    order = [
        _index_of("backup-rows.sql"),
        _index_of("--quick-gen-key"),
        _index_of("bash /scripts/backup.sh"),
        _index_of("/keys/private.asc restored"),
        _index_of("row-counts.sql"),
        _index_of("/keys/private.asc app"),
    ]
    assert order == sorted(order)
    assert len(set(order)) == len(order)


def test_two_empty_count_lists_cannot_pass_as_equal() -> None:
    counts = _commands()[_index_of("row-counts.sql")]
    assert 'test "$(wc -l < "$RUNNER_TEMP/source.txt")" -ge 3' in counts
    assert 'diff "$RUNNER_TEMP/source.txt" "$RUNNER_TEMP/restored.txt"' in counts
    assert counts.index("-ge 3") < counts.index("diff ")


def test_the_refusal_passes_only_on_the_message_of_createdb() -> None:
    refusal = _commands()[_index_of("/keys/private.asc app")]
    assert "exit 1" in refusal
    assert "grep -q 'database \"app\" already exists'" in refusal
