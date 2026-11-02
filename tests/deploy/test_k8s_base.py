"""The Kubernetes base keeps its promises: the sync order, one image for the API and the migration
pinned by digest in `prod`, a migration that is never retried, locked-down containers, and no
secret value in the files."""

import re
from pathlib import Path
from typing import Any

import yaml

from pipelines.set_image_digests import set_digests
from src.config.settings import ApiSettings, LlmGatewaySettings, Settings

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / "deploy" / "k8s" / "base"
PROD_KUSTOMIZATION = ROOT / "deploy" / "k8s" / "overlays" / "prod" / "kustomization.yaml"
COMPOSE_FILE = ROOT / "deploy" / "prod" / "compose.yml"
CADDYFILE = ROOT / "deploy" / "web" / "Caddyfile"
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


def _pod(name: str) -> dict[str, Any]:
    pod: dict[str, Any] = _object("Deployment", name)["spec"]["template"]["spec"]
    return pod


def _container(name: str) -> dict[str, Any]:
    container: dict[str, Any] = _pod(name)["containers"][0]
    return container


def test_the_base_lists_every_file_in_its_folder() -> None:
    kustomization = yaml.safe_load((BASE / "kustomization.yaml").read_text())
    on_disk = {path.name for path in BASE.glob("*.yaml")} - {"kustomization.yaml"}
    assert set(kustomization["resources"]) == on_disk


def test_the_sync_runs_the_namespace_then_the_database_then_the_migration() -> None:
    namespace = _wave("Namespace", "denial-appeal-agent")
    assert namespace < _wave("StatefulSet", "db") < _wave("Job", "migrate")
    assert _wave("Service", "db") == _wave("StatefulSet", "db")


def test_argo_cd_never_deletes_the_namespace_and_the_database_with_it() -> None:
    annotations = _object("Namespace", "denial-appeal-agent")["metadata"]["annotations"]
    assert annotations["argocd.argoproj.io/sync-options"] == "Prune=false"


def test_the_settings_exist_before_anything_reads_them() -> None:
    assert _wave("ConfigMap", "app-config") == _wave("Namespace", "denial-appeal-agent")


def test_the_api_and_web_start_after_the_migration() -> None:
    migrate = _wave("Job", "migrate")
    for kind, name in (
        ("Deployment", "api"),
        ("Service", "api"),
        ("Deployment", "web"),
        ("Service", "web"),
    ):
        assert _wave(kind, name) > migrate


def test_the_certificate_volume_is_in_the_same_wave_as_its_pod() -> None:
    # local-path makes the volume only once a pod needs it, so in an earlier wave Argo CD would wait
    # forever on a claim that the pod of a later wave has to start.
    assert _wave("PersistentVolumeClaim", "caddy-data") == _wave("Deployment", "web")


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


def test_the_api_and_the_migration_are_the_same_image_and_database() -> None:
    def url(container: dict[str, Any]) -> str:
        return str(next(e["value"] for e in container["env"] if e["name"] == "DATABASE_URL"))

    assert _container("api")["image"] == _migrate_container()["image"] == API_IMAGE
    assert url(_container("api")) == url(_migrate_container())


def test_the_password_is_listed_before_the_url_that_uses_it() -> None:
    # Kubernetes leaves `$(NAME)` as plain text when NAME comes later in the list.
    for container in (_migrate_container(), _container("api")):
        names = [entry["name"] for entry in container["env"]]
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


def test_there_is_only_one_api_and_a_new_one_starts_after_the_old_one_stops() -> None:
    # A second process would mark the first one's running jobs as interrupted.
    spec = _object("Deployment", "api")["spec"]
    assert spec["replicas"] == 1
    assert spec["strategy"] == {"type": "Recreate"}


def test_the_api_service_has_the_name_the_caddyfile_uses_and_stays_inside_the_cluster() -> None:
    assert "reverse_proxy api:8000" in CADDYFILE.read_text()
    service = _object("Service", "api")["spec"]
    assert "type" not in service
    assert [port["port"] for port in service["ports"]] == [8000]


def test_the_api_reads_the_settings_and_the_secrets() -> None:
    assert _container("api")["envFrom"] == [
        {"configMapRef": {"name": "app-config"}},
        {"secretRef": {"name": "app-secrets"}},
    ]


def test_the_api_is_ready_only_when_the_database_answers() -> None:
    api = _container("api")
    assert api["startupProbe"]["httpGet"]["path"] == "/health"
    assert api["readinessProbe"]["httpGet"]["path"] == "/health"


def test_a_database_outage_does_not_restart_the_api() -> None:
    # /health checks the database, and a restart would kill the running jobs.
    assert set(_container("api")["livenessProbe"]) & {"httpGet", "exec"} == set()
    assert "tcpSocket" in _container("api")["livenessProbe"]


def test_the_api_writes_only_to_its_temporary_folder_and_reads_the_model() -> None:
    mounts = {mount["mountPath"]: mount for mount in _container("api")["volumeMounts"]}
    assert set(mounts) == {"/tmp", "/app/models"}  # noqa: S108  (a mount path, not a temp file)
    assert mounts["/app/models"]["readOnly"] is True
    volumes = {volume["name"]: volume for volume in _pod("api")["volumes"]}
    assert volumes[mounts["/app/models"]["name"]]["configMap"]["name"] == "win-model"


def test_the_web_writes_only_to_its_certificates_and_its_config() -> None:
    mounts = {mount["mountPath"]: mount["name"] for mount in _container("web")["volumeMounts"]}
    assert set(mounts) == {"/data", "/config"}
    volumes = {volume["name"]: volume for volume in _pod("web")["volumes"]}
    assert volumes[mounts["/data"]]["persistentVolumeClaim"]["claimName"] == "caddy-data"
    assert "emptyDir" in volumes[mounts["/config"]]


def test_the_web_keeps_the_visitors_address() -> None:
    service = _object("Service", "web")["spec"]
    assert service["type"] == "LoadBalancer"
    assert service["externalTrafficPolicy"] == "Local"


def test_the_web_service_publishes_the_ports_of_the_compose_file() -> None:
    published = {
        f"{port['port']}/{port.get('protocol', 'TCP').lower()}"
        for port in _object("Service", "web")["spec"]["ports"]
    }
    compose = yaml.safe_load(COMPOSE_FILE.read_text())["services"]["web"]["ports"]
    expected = {
        f"{entry.split(':')[1]}/tcp" if "/" not in entry else entry.split(":")[1]
        for entry in compose
    }
    assert published == expected


def test_every_service_port_names_a_port_of_its_container() -> None:
    for name in ("api", "web"):
        container_ports = {port["name"] for port in _container(name)["ports"]}
        for port in _object("Service", name)["spec"]["ports"]:
            assert port["targetPort"] in container_ports


def test_the_web_gets_its_address_and_no_other_setting() -> None:
    web = _container("web")
    assert "envFrom" not in web
    assert [entry["name"] for entry in web["env"]] == ["SITE_ADDRESS"]
    reference = web["env"][0]["valueFrom"]["configMapKeyRef"]
    # Not optional: Caddy's own default is `localhost`, which would start and serve the wrong site.
    assert reference == {"name": "app-config", "key": "SITE_ADDRESS"}


def test_the_web_probes_run_inside_the_container() -> None:
    # Caddy's admin address does not answer from outside it.
    for probe in ("startupProbe", "readinessProbe", "livenessProbe"):
        assert "127.0.0.1:2019/config/" in " ".join(_container("web")[probe]["exec"]["command"])


def test_every_plain_setting_is_a_real_setting_and_none_is_a_secret() -> None:
    known = {
        field.upper()
        for model in (Settings, ApiSettings, LlmGatewaySettings)
        for field in model.model_fields
    }
    keys = set(_object("ConfigMap", "app-config")["data"])
    assert keys <= known
    assert not {key for key in keys if re.search("PASSWORD|SECRET|API_KEY|EMAILS", key)}


def test_individual_sign_up_stays_off() -> None:
    assert _object("ConfigMap", "app-config")["data"]["INDIVIDUAL_SIGNUP_ENABLED"] == "false"


def test_every_plain_setting_is_a_string() -> None:
    # Kubernetes refuses a ConfigMap whose value YAML reads as a number or a boolean.
    for value in _object("ConfigMap", "app-config")["data"].values():
        assert isinstance(value, str)


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
        assert security["capabilities"]["drop"] == ["ALL"]


def test_only_the_web_adds_a_capability_and_only_the_one_it_needs() -> None:
    # Its binary carries NET_BIND_SERVICE, so without it Caddy does not start.
    added = {
        container["name"]: container["securityContext"]["capabilities"]["add"]
        for container in _all_containers()
        if "add" in container["securityContext"]["capabilities"]
    }
    assert added == {"web": ["NET_BIND_SERVICE"]}


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
    assert "../../base" in overlay["resources"]
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
    assert "../../base" in written["resources"]
