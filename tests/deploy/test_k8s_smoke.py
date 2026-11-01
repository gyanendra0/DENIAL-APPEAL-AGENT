"""The `kind` smoke test keeps its promises: it runs on every pull request with no stored secret,
every tool is pinned and checked, and it applies the `local` overlay in the order that proves
something: images loaded first, then the migration, then `/health` through the front door, then a
pod other than `web` refused by `api`."""

import re
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "checks.yml"
SECRET_EXAMPLE = ROOT / "deploy" / "k8s" / "overlays" / "prod" / "app-secrets.example.yaml"
SMOKE = ROOT / "deploy" / "k8s" / "smoke"


def _workflow() -> dict[Any, Any]:
    workflow: dict[Any, Any] = yaml.safe_load(WORKFLOW.read_text())
    return workflow


def _job() -> dict[str, Any]:
    job: dict[str, Any] = _workflow()["jobs"]["smoke"]
    return job


def _commands() -> list[str]:
    return [step.get("run", "") for step in _job()["steps"]]


def _index_of(text: str) -> int:
    return next(i for i, command in enumerate(_commands()) if text in command)


def test_every_pull_request_runs_the_smoke_test() -> None:
    # PyYAML reads the bare key `on` as True.
    assert "pull_request" in _workflow()[True]


def test_the_smoke_test_only_reads_the_repository_and_holds_no_secret() -> None:
    job = _job()
    assert job["permissions"] == {"contents": "read"}
    assert "environment" not in job
    assert "secrets." not in WORKFLOW.read_text()
    checkout = job["steps"][0]
    assert checkout["with"]["persist-credentials"] is False


def test_every_tool_is_pinned_and_its_checksum_verified() -> None:
    env = _job()["env"]
    for tool in ("KIND", "KUBECTL", "KUSTOMIZE"):
        assert re.fullmatch(r"\d+\.\d+\.\d+", env[f"{tool}_VERSION"])
        assert re.fullmatch(r"[0-9a-f]{64}", env[f"{tool}_SHA256"])

    installs = [c for c in _commands() if "curl -fsSL" in c]
    assert len(installs) == 3
    for install in installs:
        assert "sha256sum -c -" in install


def test_the_cluster_uses_the_pinned_node_image() -> None:
    assert re.fullmatch(
        r"kindest/node:v[\d.]+@sha256:[0-9a-f]{64}", _job()["env"]["KIND_NODE_IMAGE"]
    )
    assert '--image "$KIND_NODE_IMAGE"' in _commands()[_index_of(" create cluster")]


def test_the_pull_requests_own_images_are_built_and_loaded_under_the_local_tag() -> None:
    env = _job()["env"]
    overlay = yaml.safe_load((ROOT / "deploy/k8s/overlays/local/kustomization.yaml").read_text())
    expected = {f"{image['name']}:{image['newTag']}" for image in overlay["images"]}
    assert {env["API_IMAGE"], env["WEB_IMAGE"]} == expected

    load = _commands()[_index_of("load docker-image")]
    assert '"$API_IMAGE"' in load
    assert '"$WEB_IMAGE"' in load
    assert _index_of('-t "$API_IMAGE"') < _index_of("load docker-image")
    assert _index_of('-t "$WEB_IMAGE"') < _index_of("load docker-image")


def test_the_made_up_secret_has_every_key_production_has() -> None:
    text = SECRET_EXAMPLE.read_text()
    expected = set(yaml.safe_load(text)["stringData"])
    create = _commands()[_index_of("create secret generic app-secrets")]
    assert set(re.findall(r"--from-literal=([A-Z_]+)=", create)) == expected


def test_the_model_is_made_in_the_api_image_and_becomes_the_configmap() -> None:
    create = _commands()[_index_of("create configmap win-model")]
    make = create.index("pipelines.make_smoke_model")
    assert '"$API_IMAGE"' in create[:make]
    assert make < create.index("create configmap win-model")
    assert '--from-file="$RUNNER_TEMP/model"' in create


def test_the_secret_and_model_exist_before_the_overlay_is_applied() -> None:
    assert _index_of("create secret generic") < _index_of('apply -f "$RUNNER_TEMP/local.yaml"')
    assert _index_of("create configmap win-model") < _index_of('apply -f "$RUNNER_TEMP/local.yaml"')


def test_the_local_overlay_is_the_one_applied() -> None:
    assert 'kustomize" build deploy/k8s/overlays/local' in _commands()[_index_of("local.yaml")]
    assert "overlays/prod" not in WORKFLOW.read_text()


def test_the_steps_run_in_the_order_that_proves_the_migration_and_health() -> None:
    order = [
        _index_of("load docker-image"),
        _index_of('apply -f "$RUNNER_TEMP/local.yaml"'),
        _index_of("--for=condition=complete job/migrate"),
        _index_of("rollout status"),
        _index_of("/health"),
    ]
    assert order == sorted(order)


def test_all_three_workloads_must_become_ready() -> None:
    wait = _commands()[_index_of("rollout status")]
    for workload in ("statefulset/db", "deployment/api", "deployment/web"):
        assert workload in wait


def test_health_is_asked_through_the_front_door_and_a_bad_answer_fails_the_step() -> None:
    check = _commands()[_index_of("/health")]
    assert "port-forward service/web 8080:80" in check
    assert re.search(r"curl -\w*f\w*", check)
    assert "--retry-all-errors" in check
    assert "http://127.0.0.1:8080/health" in check


def test_the_refusal_is_tried_after_health_with_a_working_probe_first() -> None:
    check = _commands()[_index_of("deploy/k8s/smoke/probe.yaml")]
    assert _index_of("/health") < _index_of("deploy/k8s/smoke/probe.yaml")
    order = [
        check.index("apply -f deploy/k8s/smoke/probe.yaml"),
        check.index("apply -f deploy/k8s/smoke/probe-allowed.yaml"),
        check.index('test "$reached" = yes'),
        check.index("delete -f deploy/k8s/smoke/probe-allowed.yaml"),
        check.index('test "$refused" = yes'),
    ]
    assert order == sorted(order)


def test_only_a_timeout_counts_as_a_refusal() -> None:
    check = _commands()[_index_of("deploy/k8s/smoke/probe.yaml")]
    assert "except TimeoutError:" in check
    assert "sys.exit(3)" in check
    assert 'if [ "$result" -eq 3 ]; then refused=yes' in check
    assert 'if [ "$result" -ne 0 ]; then exit "$result"' in check
    assert 'if [ "$result" -ne 3 ]; then exit "$result"' in check


def test_the_probe_is_not_web_and_only_default_deny_of_its_own_is_lifted() -> None:
    probe = {d["kind"]: d for d in yaml.safe_load_all((SMOKE / "probe.yaml").read_text())}
    labels = probe["Pod"]["metadata"]["labels"]
    assert labels == {"app.kubernetes.io/name": "probe"}
    assert probe["NetworkPolicy"]["spec"]["podSelector"]["matchLabels"] == labels
    assert probe["NetworkPolicy"]["spec"]["policyTypes"] == ["Egress"]
    to_api = probe["NetworkPolicy"]["spec"]["egress"][0]
    assert to_api["to"] == [{"podSelector": {"matchLabels": {"app.kubernetes.io/name": "api"}}}]
    assert to_api["ports"] == [{"port": 8000}]


def test_the_probe_runs_the_image_the_job_loaded() -> None:
    documents = yaml.safe_load_all((SMOKE / "probe.yaml").read_text())
    pod = next(d for d in documents if d["kind"] == "Pod")
    container = pod["spec"]["containers"][0]
    assert container["image"] == _job()["env"]["API_IMAGE"]
    assert container["imagePullPolicy"] == "Never"


def test_the_moment_of_allowance_opens_the_api_to_the_probe_only() -> None:
    allowed = yaml.safe_load((SMOKE / "probe-allowed.yaml").read_text())
    assert allowed["spec"]["podSelector"]["matchLabels"] == {"app.kubernetes.io/name": "api"}
    assert allowed["spec"]["ingress"] == [
        {
            "from": [{"podSelector": {"matchLabels": {"app.kubernetes.io/name": "probe"}}}],
            "ports": [{"port": 8000}],
        }
    ]


def test_argo_cd_never_applies_the_probe() -> None:
    base = yaml.safe_load((ROOT / "deploy/k8s/base/kustomization.yaml").read_text())
    assert not [resource for resource in base["resources"] if "smoke" in resource]


def test_the_diagnostics_run_only_after_a_failure_and_come_last() -> None:
    steps = _job()["steps"]
    last = steps[-1]
    assert last["if"] == "failure()"
    assert all("if" not in step for step in steps[:-1])
