"""The nightly backup keeps its promises: one run a day at 03:00 UTC, never two at once, suspended
until the real public key is in, a dump that fails leaves no file to upload, and each container
gets only the secret it needs."""

import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / "deploy" / "k8s" / "base"
SCRIPT = BASE / "backup" / "backup.sh"
PUBLIC_KEY = BASE / "backup" / "backup-public-key.asc"
RCLONE = re.compile(r"rclone/rclone:1\.75\.2@sha256:[0-9a-f]{64}")


def _documents() -> list[dict[str, Any]]:
    return list(yaml.safe_load_all((BASE / "backup.yaml").read_text()))


def _cronjob() -> dict[str, Any]:
    (cronjob,) = [d for d in _documents() if d["kind"] == "CronJob"]
    return cronjob


def _pod() -> dict[str, Any]:
    pod: dict[str, Any] = _cronjob()["spec"]["jobTemplate"]["spec"]["template"]["spec"]
    return pod


def _dump() -> dict[str, Any]:
    dump: dict[str, Any] = _pod()["initContainers"][0]
    assert len(_pod()["initContainers"]) == 1
    return dump


def _upload() -> dict[str, Any]:
    upload: dict[str, Any] = _pod()["containers"][0]
    assert len(_pod()["containers"]) == 1
    return upload


def _settings() -> dict[str, str]:
    (config,) = [d for d in _documents() if d["kind"] == "ConfigMap"]
    data: dict[str, str] = config["data"]
    return data


def _generator() -> dict[str, Any]:
    kustomization = yaml.safe_load((BASE / "kustomization.yaml").read_text())
    generator: dict[str, Any] = kustomization["configMapGenerator"][0]
    assert len(kustomization["configMapGenerator"]) == 1
    return generator


def test_the_backup_runs_once_a_day_at_three_utc_and_never_twice_at_once() -> None:
    spec = _cronjob()["spec"]
    assert (spec["schedule"], spec["timeZone"]) == ("0 3 * * *", "Etc/UTC")
    assert spec["concurrencyPolicy"] == "Forbid"
    assert spec["jobTemplate"]["spec"]["backoffLimit"] == 1
    assert spec["jobTemplate"]["spec"]["activeDeadlineSeconds"] <= 3600


def test_the_backup_stays_suspended_while_the_public_key_is_a_placeholder() -> None:
    if "BEGIN PGP PUBLIC KEY BLOCK" not in PUBLIC_KEY.read_text():
        assert _cronjob()["spec"]["suspend"] is True


def test_the_script_and_the_key_are_mounted_from_the_generated_files() -> None:
    generator = _generator()
    assert generator["name"] == "backup-files"
    assert generator["files"] == ["backup/backup.sh", "backup/backup-public-key.asc"]
    volumes = {volume["name"]: volume for volume in _pod()["volumes"]}
    assert volumes["scripts"]["configMap"]["name"] == "backup-files"
    assert _dump()["command"] == [
        "bash",
        "/scripts/backup.sh",
        "/backup",
        "/scripts/backup-public-key.asc",
    ]


def test_the_dump_reads_the_database_with_only_its_password() -> None:
    env = {entry["name"]: entry for entry in _dump()["env"]}
    assert (env["PGHOST"]["value"], env["PGUSER"]["value"], env["PGDATABASE"]["value"]) == (
        "db",
        "app",
        "app",
    )
    from_secrets = [e["valueFrom"]["secretKeyRef"] for e in env.values() if "valueFrom" in e]
    assert from_secrets == [{"name": "app-secrets", "key": "POSTGRES_PASSWORD"}]
    assert "envFrom" not in _dump()


def test_the_upload_runs_the_pinned_rclone_with_only_the_bucket_keys() -> None:
    upload = _upload()
    assert RCLONE.fullmatch(upload["image"])
    assert upload["envFrom"] == [
        {"configMapRef": {"name": "backup-config"}},
        {"secretRef": {"name": "backup-secrets"}},
    ]
    assert "env" not in upload
    assert upload["args"][:3] == ["copy", "/backup", "oci:$(BACKUP_BUCKET)/app"]


def test_the_upload_only_reads_the_dump() -> None:
    mounts = _upload()["volumeMounts"]
    assert mounts == [{"name": "backup", "mountPath": "/backup", "readOnly": True}]


def test_rclone_finds_the_bucket_without_listing_or_creating_one() -> None:
    settings = _settings()
    assert settings["RCLONE_CONFIG_OCI_TYPE"] == "s3"
    assert settings["RCLONE_CONFIG_OCI_PROVIDER"] == "Other"
    assert settings["RCLONE_CONFIG_OCI_FORCE_PATH_STYLE"] == "true"
    assert settings["RCLONE_CONFIG_OCI_NO_CHECK_BUCKET"] == "true"
    assert all(isinstance(value, str) for value in settings.values())
    assert not [key for key in settings if re.search("SECRET|ACCESS_KEY|PASSWORD", key)]


def test_a_failed_dump_leaves_no_file_to_upload() -> None:
    text = SCRIPT.read_text()
    assert text.startswith("#!/bin/bash\n")
    # Without pipefail, gpg's success would hide a failed pg_dump.
    assert "set -euo pipefail" in text
    assert '--output "$out.part"' in text
    assert 'mv "$out.part" "$out"' in text
    assert "trap 'rm -f \"$out.part\"' ERR" in text


def test_the_dump_is_encrypted_to_the_public_key_and_written_where_gpg_may_write() -> None:
    text = SCRIPT.read_text()
    assert "pg_dump --format=custom" in text
    assert '--recipient-file "$public_key"' in text
    assert 'GNUPGHOME="$(mktemp -d)"' in text
    assert "--symmetric" not in text


def test_the_script_is_valid_bash() -> None:
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("needs bash")
    # Only reads our own committed file; nothing from outside reaches the command.
    result = subprocess.run(  # noqa: S603
        [bash, "-n", str(SCRIPT)], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr


def test_the_dump_folder_and_temporary_folder_are_bounded() -> None:
    volumes = {volume["name"]: volume for volume in _pod()["volumes"]}
    assert volumes["backup"]["emptyDir"]["sizeLimit"] == "512Mi"
    assert volumes["tmp"]["emptyDir"]["sizeLimit"] == "16Mi"
