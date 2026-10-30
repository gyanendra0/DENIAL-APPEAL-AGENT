"""The Dependabot config watches every place a version is pinned, on the promised terms."""

from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
CONFIG_FILE = ROOT / ".github" / "dependabot.yml"


def _updates() -> dict[str, dict[str, Any]]:
    """Each `updates` entry, by its ecosystem."""
    config = yaml.safe_load(CONFIG_FILE.read_text())
    return {entry["package-ecosystem"]: entry for entry in config["updates"]}


def _directories(entry: dict[str, Any]) -> list[str]:
    directories: list[str] = (
        entry["directories"] if "directories" in entry else [entry["directory"]]
    )
    return directories


def test_every_ecosystem_is_watched_in_its_own_directories() -> None:
    watched = {name: _directories(entry) for name, entry in _updates().items()}
    assert watched == {
        "github-actions": ["/"],
        "pip": ["/deploy/api"],
        "npm": ["/frontend"],
        "docker": ["/deploy/api", "/deploy/web"],
        "docker-compose": ["/deploy/prod"],
    }


def test_every_ecosystem_is_weekly_grouped_and_cooled_down() -> None:
    for entry in _updates().values():
        assert entry["schedule"]["interval"] == "weekly"
        assert entry["cooldown"]["default-days"] == 7
        assert [group["patterns"] for group in entry["groups"].values()] == [["*"]]


def test_every_commit_is_a_conventional_chore() -> None:
    for entry in _updates().values():
        assert entry["commit-message"] == {"prefix": "chore", "include": "scope"}


def test_the_pip_directory_holds_a_file_dependabot_can_read() -> None:
    # Dependabot's pip support reads only .txt and .in files, and skips anything else in silence.
    assert (ROOT / "deploy" / "api" / "requirements.txt").is_file()
    assert not list((ROOT / "deploy" / "api").glob("*.lock"))


def test_python_stays_on_its_version_and_the_other_images_on_their_major() -> None:
    def blocked(ecosystem: str) -> dict[str, set[str]]:
        rules = _updates()[ecosystem]["ignore"]
        return {rule["dependency-name"]: set(rule["update-types"]) for rule in rules}

    major = "version-update:semver-major"
    minor = "version-update:semver-minor"
    assert blocked("docker") == {"python": {major, minor}, "node": {major}, "caddy": {major}}
    assert blocked("docker-compose") == {"pgvector/pgvector": {major}}
