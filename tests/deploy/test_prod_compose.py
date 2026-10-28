"""The production compose file keeps its promises: only the web container is reachable, the API
trusts only the web container's address, migrations run before the API, the database image is
pinned and its data is kept, and no secret is written in the file."""

import re
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
COMPOSE_FILE = ROOT / "deploy" / "prod" / "compose.yml"


def _services() -> dict[str, Any]:
    compose = yaml.safe_load(COMPOSE_FILE.read_text())
    services: dict[str, Any] = compose["services"]
    return services


def test_the_file_has_the_four_services() -> None:
    assert set(_services()) == {"db", "migrate", "api", "web"}


def test_only_the_web_container_publishes_ports() -> None:
    published = {name for name, service in _services().items() if service.get("ports")}
    assert published == {"web"}


def test_the_web_container_publishes_only_http_and_https() -> None:
    ports = _services()["web"]["ports"]
    assert sorted(ports) == ["443:443", "443:443/udp", "80:80"]


def test_the_api_trusts_only_the_web_containers_fixed_address() -> None:
    services = _services()
    web_address = services["web"]["networks"]["app"]["ipv4_address"]
    trusted = services["api"]["environment"]["FORWARDED_ALLOW_IPS"]
    assert trusted == web_address
    assert "*" not in trusted


def test_the_api_starts_only_after_the_migrations_succeed() -> None:
    services = _services()
    waits_for = services["api"]["depends_on"]
    assert waits_for["migrate"] == {"condition": "service_completed_successfully"}
    assert waits_for["db"] == {"condition": "service_healthy"}
    assert services["migrate"]["command"] == ["alembic", "upgrade", "head"]
    assert services["migrate"]["restart"] == "no"


def test_migrate_and_api_run_the_same_image() -> None:
    services = _services()
    assert services["migrate"]["image"] == services["api"]["image"]


def test_the_database_image_is_pinned_by_digest() -> None:
    assert re.search(r"@sha256:[0-9a-f]{64}$", _services()["db"]["image"])


def test_the_database_keeps_its_data_on_a_named_volume() -> None:
    compose = yaml.safe_load(COMPOSE_FILE.read_text())
    (volume,) = compose["services"]["db"]["volumes"]
    name, target = volume.split(":")
    assert target == "/var/lib/postgresql/data"
    assert name in compose["volumes"]


def test_the_model_files_are_mounted_read_only() -> None:
    (volume,) = _services()["api"]["volumes"]
    assert volume.endswith(":/app/models:ro")


def test_no_password_is_written_in_the_file() -> None:
    # Every password is a required ${POSTGRES_PASSWORD:?...} value from deploy/prod/.env.
    services = _services()
    assert services["db"]["environment"]["POSTGRES_PASSWORD"].startswith("${POSTGRES_PASSWORD:?")
    for name in ("migrate", "api"):
        url = services[name]["environment"]["DATABASE_URL"]
        assert re.fullmatch(
            r"postgresql\+psycopg://app:\$\{POSTGRES_PASSWORD:\?[^}]*\}@db:5432/app", url
        )


def test_the_project_name_differs_from_the_development_compose_project() -> None:
    # The root compose file has no name, so its project is the folder's name; the same name here
    # would make this `db` replace the development database container and use its volume.
    compose = yaml.safe_load(COMPOSE_FILE.read_text())
    assert "name" not in yaml.safe_load((ROOT / "docker-compose.yml").read_text())
    assert compose["name"] != ROOT.name
