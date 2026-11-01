"""The Kubernetes base keeps its promises: the sync order, one image for the API and the migration
pinned by digest in `prod`, a migration that is never retried, locked-down containers, and no
secret value in the files."""

import re
from pathlib import Path
from typing import Any

import yaml

from pipelines.set_image_digests import set_digests

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / "deploy" / "k8s" / "base"
PROD_KUSTOMIZATION = ROOT / "deploy" / "k8s" / "overlays" / "prod" / "kustomization.yaml"
COMPOSE_FILE = ROOT / "deploy" / "prod" / "compose.yml"
API_IMAGE = "ghcr.io/gyanendra0/denial-appeal-agent/api"
WAVE = "argocd.argoproj.io/sync-wave"


def _objects() -> list[dict[str, Any]]:
    """Every object in the files the base lists."""
    kustomization = yaml.safe_load((BASE / "kustomization.yaml").read_text())
    objects: list[dict[str, Any]] = []
    for name in kustomization["resources"]:
        objects.extend(yaml.safe_load_all((BASE / name).read_text()))
    return objects


def _object(kind: str, name: str) -> dict[str, Any]:
    found = [o for o in _objects() if o["kind"] == kind and o["metadata"]["name"] == name]
    assert len(found) == 1
    return found[0]


def _pod_specs() -> list[dict[str, Any]]:
    return [o["spec"]["template"]["spec"] for o in _objects() if "template" in o.get("spec", {})]


def _containers(pod: dict[str, Any]) -> list[dict[str, Any]]:
    containers: list[dict[str, Any]] = pod["containers"] + pod.get("initContainers", [])
    return containers


def _all_containers() -> list[dict[str, Any]]:
    return [container for pod in _pod_specs() for container in _containers(pod)]


def _wave(kind: str, name: str) -> int:
    return int(_object(kind, name)["metadata"]["annotations"][WAVE])


def _migrate_container() -> dict[str, Any]:
    container: dict[str, Any] = _object("Job", "migrate")["spec"]["template"]["spec"]["containers"][
        0
    ]
    return container


def test_the_base_lists_every_file_in_its_folder() -> None:
    kustomization = yaml.safe_load((BASE / "kustomization.yaml").read_text())
    on_disk = {path.name for path in BASE.glob("*.yaml")} - {"kustomization.yaml"}
    assert set(kustomization["resources"]) == on_disk


def test_the_sync_runs_the_namespace_then_the_database_then_the_migration() -> None:
    namespace = _wave("Namespace", "denial-appeal-agent")
    assert namespace < _wave("StatefulSet", "db") < _wave("Job", "migrate")
    assert _wave("Service", "db") == _wave("StatefulSet", "db")


def test_the_migration_is_a_sync_hook_so_it_can_run_after_the_database() -> None:
    annotations = _object("Job", "migrate")["metadata"]["annotations"]
    # A PreSync hook would run before the database exists on the very first sync.
    assert annotations["argocd.argoproj.io/hook"] == "Sync"
    assert annotations["argocd.argoproj.io/hook-delete-policy"] == "BeforeHookCreation"


def test_a_failed_migration_is_never_retried() -> None:
    job = _object("Job", "migrate")["spec"]
    assert job["backoffLimit"] == 0
    assert job["template"]["spec"]["restartPolicy"] == "Never"
    assert job["activeDeadlineSeconds"] > 0


def test_the_migration_waits_for_the_database() -> None:
    pod = _object("Job", "migrate")["spec"]["template"]["spec"]
    assert "pg_isready -h db" in " ".join(pod["initContainers"][0]["command"])


def test_the_migration_runs_the_api_image_and_alembic() -> None:
    migrate = _migrate_container()
    assert migrate["image"] == API_IMAGE
    assert migrate["command"] == ["alembic", "upgrade", "head"]


def test_the_password_is_listed_before_the_url_that_uses_it() -> None:
    # Kubernetes leaves `$(NAME)` as plain text when NAME comes later in the list.
    names = [entry["name"] for entry in _migrate_container()["env"]]
    assert names.index("POSTGRES_PASSWORD") < names.index("DATABASE_URL")


def test_the_migration_gets_no_secret_but_the_database_password() -> None:
    migrate = _migrate_container()
    assert "envFrom" not in migrate
    from_secrets = [
        entry["valueFrom"]["secretKeyRef"]["key"]
        for entry in migrate["env"]
        if "valueFrom" in entry
    ]
    assert from_secrets == ["POSTGRES_PASSWORD"]


def test_the_database_image_is_the_one_in_the_compose_file() -> None:
    # Dependabot watches the compose file. This fails its update until the copies here move too.
    compose = yaml.safe_load(COMPOSE_FILE.read_text())
    pinned = compose["services"]["db"]["image"]
    assert re.search(r"@sha256:[0-9a-f]{64}$", pinned)
    database = _object("StatefulSet", "db")["spec"]["template"]["spec"]["containers"][0]
    wait = _object("Job", "migrate")["spec"]["template"]["spec"]["initContainers"][0]
    assert database["image"] == pinned
    assert wait["image"] == pinned


def test_the_database_keeps_its_data_in_a_subfolder_of_a_kept_volume() -> None:
    spec = _object("StatefulSet", "db")["spec"]
    container = spec["template"]["spec"]["containers"][0]
    mounts = {mount["name"]: mount["mountPath"] for mount in container["volumeMounts"]}
    data_folder = next(e["value"] for e in container["env"] if e["name"] == "PGDATA")
    assert [claim["metadata"]["name"] for claim in spec["volumeClaimTemplates"]] == ["data"]
    assert data_folder.startswith(mounts["data"] + "/")


def test_the_database_writes_only_where_it_must() -> None:
    container = _object("StatefulSet", "db")["spec"]["template"]["spec"]["containers"][0]
    assert {mount["mountPath"] for mount in container["volumeMounts"]} == {
        "/var/lib/postgresql/data",
        "/var/run/postgresql",
        "/tmp",  # noqa: S108  (a mount path in the manifest, not a temp file)
    }


def test_the_database_service_publishes_no_port_outside_the_cluster() -> None:
    assert _object("Service", "db")["spec"]["clusterIP"] == "None"


def test_every_database_probe_asks_over_tcp() -> None:
    # The first start's temporary server answers on the socket only, so a check there would call
    # the database ready just before it stops.
    container = _object("StatefulSet", "db")["spec"]["template"]["spec"]["containers"][0]
    for probe in ("startupProbe", "readinessProbe", "livenessProbe"):
        command = container[probe]["exec"]["command"]
        assert command[:3] == ["pg_isready", "-h", "127.0.0.1"]


def test_every_pod_runs_as_non_root_with_no_token_and_the_default_seccomp() -> None:
    pods = _pod_specs()
    assert pods
    for pod in pods:
        assert pod["automountServiceAccountToken"] is False
        assert pod["securityContext"]["runAsNonRoot"] is True
        assert pod["securityContext"]["runAsUser"] > 0
        assert pod["securityContext"]["seccompProfile"] == {"type": "RuntimeDefault"}


def test_every_container_is_locked_down() -> None:
    containers = _all_containers()
    assert containers
    for container in containers:
        security = container["securityContext"]
        assert security["allowPrivilegeEscalation"] is False
        assert security["readOnlyRootFilesystem"] is True
        assert security["capabilities"] == {"drop": ["ALL"]}


def test_every_container_has_requests_and_limits() -> None:
    for container in _all_containers():
        for bound in ("requests", "limits"):
            assert set(container["resources"][bound]) == {"cpu", "memory"}


def test_no_secret_value_is_written_in_the_files() -> None:
    for container in _all_containers():
        for entry in container.get("env", []):
            if "PASSWORD" in entry["name"]:
                assert "value" not in entry
    for path in BASE.glob("*.yaml"):
        assert not re.search(
            r"postgresql\+psycopg://app:(?!\$\(POSTGRES_PASSWORD\))", path.read_text()
        )


def test_prod_uses_the_base_and_pins_every_published_image_by_digest() -> None:
    overlay = yaml.safe_load(PROD_KUSTOMIZATION.read_text())
    assert overlay["resources"] == ["../../base"]
    for image in overlay["images"]:
        assert re.fullmatch(r"sha256:[0-9a-f]{64}", image["digest"])
    pinned = {image["name"] for image in overlay["images"]}
    used = {container["image"] for container in _all_containers()}
    assert {image for image in used if "@sha256:" not in image} <= pinned


def test_the_deploy_script_changes_only_the_digests_of_the_overlay() -> None:
    text = PROD_KUSTOMIZATION.read_text()
    images = yaml.safe_load(text)["images"]
    digests = {image["name"]: f"sha256:{'a' * 64}" for image in images}

    written = yaml.safe_load(set_digests(text, digests))

    assert {image["name"]: image["digest"] for image in written["images"]} == digests
    assert written["resources"] == ["../../base"]
