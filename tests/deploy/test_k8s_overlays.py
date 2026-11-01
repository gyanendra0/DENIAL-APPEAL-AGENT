"""The overlays, the network rules and the Argo CD `Application` keep their promises: each overlay
gives the app every setting it needs, no secret value is in git, nothing talks to anything unless a
rule allows it, and Argo CD follows the digest-pinned `prod` overlay."""

import ipaddress
import re
from pathlib import Path
from typing import Any

import pytest
import yaml

from src.config.settings import ApiSettings, LlmGatewaySettings, Settings

ROOT = Path(__file__).resolve().parents[2]
K8S = ROOT / "deploy" / "k8s"
BASE_CONFIG = K8S / "base" / "config.yaml"
POLICIES = K8S / "base" / "network-policies.yaml"
OVERLAYS = K8S / "overlays"
SECRET_TEMPLATE = OVERLAYS / "prod" / "app-secrets.example.yaml"
APPLICATION = K8S / "argocd" / "application.yaml"
METADATA_ADDRESS = ipaddress.ip_address("169.254.169.254")
POD_RANGES = ("10.42.0.0", "10.244.0.0")
WAVE = "argocd.argoproj.io/sync-wave"
# Shaped to pass the settings' checks; the keys are the ones in the template.
MADE_UP_SECRETS = {
    "POSTGRES_PASSWORD": "made-up",
    "AUTH_TOKEN_SECRET": "made-up-" * 8,
    "LLM_PRIMARY_API_KEY": "made-up",
    "LLM_FALLBACK_API_KEY": "made-up",
    "MAIL_SMTP_USERNAME": "made-up",
    "MAIL_SMTP_PASSWORD": "made-up",
    "MAIL_FROM": "mail@example.test",
    "GOOGLE_CLIENT_SECRET": "made-up",
    "ADMIN_EMAILS": "admin@example.test",
    "SIGNUP_ALLOWED_EMAILS": "user@example.test",
}


def _overlay_settings(name: str) -> dict[str, str]:
    data: dict[str, str] = yaml.safe_load((OVERLAYS / name / "config.yaml").read_text())["data"]
    return data


def _all_plain_settings(name: str) -> dict[str, str]:
    base: dict[str, str] = yaml.safe_load(BASE_CONFIG.read_text())["data"]
    return base | _overlay_settings(name)


def _secret_keys() -> set[str]:
    return set(yaml.safe_load(SECRET_TEMPLATE.read_text())["stringData"])


def _policies() -> dict[str, dict[str, Any]]:
    return {p["metadata"]["name"]: p for p in yaml.safe_load_all(POLICIES.read_text())}


def _pods_named(peers: list[dict[str, Any]]) -> set[str]:
    return {
        peer["podSelector"]["matchLabels"]["app.kubernetes.io/name"]
        for peer in peers
        if "app.kubernetes.io/name" in peer.get("podSelector", {}).get("matchLabels", {})
    }


def _ip_blocks(policy: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        peer["ipBlock"]
        for rule in policy["spec"].get("egress", [])
        for peer in rule["to"]
        if "ipBlock" in peer
    ]


def _internet_ports(policy: dict[str, Any]) -> set[int]:
    return {
        port["port"]
        for rule in policy["spec"].get("egress", [])
        if any("ipBlock" in peer for peer in rule["to"])
        for port in rule["ports"]
    }


@pytest.mark.parametrize("overlay", ["local", "prod"])
def test_each_overlay_gives_the_app_valid_settings(
    overlay: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    for key, value in (_all_plain_settings(overlay) | MADE_UP_SECRETS).items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://app:made-up@db:5432/app")

    Settings(_env_file=None)  # type: ignore[call-arg]
    ApiSettings(_env_file=None)  # type: ignore[call-arg]
    LlmGatewaySettings(_env_file=None)  # type: ignore[call-arg]


def test_the_made_up_secrets_are_the_keys_of_the_template() -> None:
    assert set(MADE_UP_SECRETS) == _secret_keys()


@pytest.mark.parametrize("overlay", ["local", "prod"])
def test_every_overlay_setting_is_a_plain_string_and_a_real_setting(overlay: str) -> None:
    known = {
        field.upper()
        for model in (Settings, ApiSettings, LlmGatewaySettings)
        for field in model.model_fields
    }
    settings = _overlay_settings(overlay)
    # Read by Caddy and uvicorn, not by the settings module.
    assert set(settings) - {"SITE_ADDRESS", "FORWARDED_ALLOW_IPS"} <= known
    assert all(isinstance(value, str) for value in settings.values())


@pytest.mark.parametrize("overlay", ["local", "prod"])
def test_no_overlay_setting_is_a_secret_or_an_email_address(overlay: str) -> None:
    settings = _overlay_settings(overlay)
    assert not set(settings) & _secret_keys()
    assert not [value for value in settings.values() if "@" in value]


@pytest.mark.parametrize("overlay", ["local", "prod"])
def test_each_overlay_sets_the_keys_the_containers_need_and_the_base_leaves_out(
    overlay: str,
) -> None:
    assert {"SITE_ADDRESS", "FORWARDED_ALLOW_IPS"} <= set(_overlay_settings(overlay))


def test_production_is_served_over_https_at_its_own_name() -> None:
    settings = _overlay_settings("prod")
    assert settings["APP_PUBLIC_URL"] == f"https://{settings['SITE_ADDRESS']}"


def test_local_reaches_no_real_provider_or_mail_server() -> None:
    settings = _overlay_settings("local")
    assert settings["LLM_PRIMARY_BASE_URL"].endswith(".invalid/v1")
    assert settings["MAIL_SMTP_HOST"].endswith(".invalid")


@pytest.mark.parametrize(
    ("overlay", "pod_range"), [("local", "10.244.0.0/16"), ("prod", "10.42.0.0/16")]
)
def test_the_api_trusts_only_the_pod_range_of_its_cluster(overlay: str, pod_range: str) -> None:
    assert _overlay_settings(overlay)["FORWARDED_ALLOW_IPS"] == pod_range


def test_local_uses_a_plain_tag_on_the_same_images_as_prod() -> None:
    local = yaml.safe_load((OVERLAYS / "local" / "kustomization.yaml").read_text())["images"]
    prod = yaml.safe_load((OVERLAYS / "prod" / "kustomization.yaml").read_text())["images"]
    assert {image["name"] for image in local} == {image["name"] for image in prod}
    assert all("digest" not in image and image["newTag"] == "local" for image in local)


@pytest.mark.parametrize("overlay", ["local", "prod"])
def test_each_overlay_patches_the_settings_of_the_base(overlay: str) -> None:
    kustomization = yaml.safe_load((OVERLAYS / overlay / "kustomization.yaml").read_text())
    assert kustomization["resources"] == ["../../base"]
    assert kustomization["patches"] == [{"path": "config.yaml"}]
    patch = yaml.safe_load((OVERLAYS / overlay / "config.yaml").read_text())
    assert (patch["kind"], patch["metadata"]["name"]) == ("ConfigMap", "app-config")


def test_the_secret_template_holds_no_value() -> None:
    template = yaml.safe_load(SECRET_TEMPLATE.read_text())
    assert template["metadata"]["name"] == "app-secrets"
    assert set(template["stringData"].values()) == {""}


def test_an_empty_secret_can_never_reach_the_cluster() -> None:
    prod = yaml.safe_load((OVERLAYS / "prod" / "kustomization.yaml").read_text())
    assert SECRET_TEMPLATE.name not in prod["resources"]


def test_the_template_has_every_key_the_pods_read_by_name() -> None:
    for name in ("api.yaml", "migrate.yaml"):
        text = (K8S / "base" / name).read_text()
        assert set(re.findall(r"key: ([A-Z_]+)", text)) <= _secret_keys()


def test_a_pod_with_no_rule_can_do_nothing() -> None:
    deny = _policies()["default-deny"]["spec"]
    assert deny == {"podSelector": {}, "policyTypes": ["Ingress", "Egress"]}


def test_every_app_has_a_rule_that_limits_both_directions() -> None:
    policies = _policies()
    for name in ("web", "api", "migrate", "db"):
        spec = policies[name]["spec"]
        assert spec["podSelector"]["matchLabels"] == {"app.kubernetes.io/name": name}
        assert spec["policyTypes"] == ["Ingress", "Egress"]


def test_the_network_rules_exist_before_any_pod_starts() -> None:
    for policy in _policies().values():
        assert int(policy["metadata"]["annotations"][WAVE]) == -2


def test_only_the_web_reaches_the_api_and_only_on_its_port() -> None:
    rules = _policies()["api"]["spec"]["ingress"]
    assert len(rules) == 1
    assert _pods_named(rules[0]["from"]) == {"web"}
    assert rules[0]["ports"] == [{"port": 8000}]


def test_only_the_api_and_the_migration_reach_the_database() -> None:
    rules = _policies()["db"]["spec"]["ingress"]
    assert len(rules) == 1
    assert _pods_named(rules[0]["from"]) == {"api", "migrate"}
    assert rules[0]["ports"] == [{"port": 5432}]


def test_the_database_opens_no_connection() -> None:
    assert "egress" not in _policies()["db"]["spec"]


def test_nothing_reaches_the_migration_and_it_has_no_internet() -> None:
    migrate = _policies()["migrate"]
    assert "ingress" not in migrate["spec"]
    assert not _ip_blocks(migrate)


def test_anyone_may_reach_the_web_on_the_ports_of_its_service() -> None:
    rules = _policies()["web"]["spec"]["ingress"]
    assert len(rules) == 1
    assert "from" not in rules[0]
    assert rules[0]["ports"] == [{"port": 80}, {"port": 443}, {"port": 443, "protocol": "UDP"}]


@pytest.mark.parametrize(
    ("name", "ports"), [("web", {80, 443}), ("api", {443, 587})], ids=["web", "api"]
)
def test_the_internet_is_open_only_on_the_ports_each_app_uses(name: str, ports: set[int]) -> None:
    assert _internet_ports(_policies()[name]) == ports


@pytest.mark.parametrize("name", ["web", "api"])
def test_the_internet_rule_cannot_reach_the_metadata_address_or_a_pod(name: str) -> None:
    blocks = _ip_blocks(_policies()[name])
    assert blocks
    for block in blocks:
        kept_out = [ipaddress.ip_network(cidr) for cidr in block["except"]]
        for address in (METADATA_ADDRESS, *map(ipaddress.ip_address, POD_RANGES)):
            assert any(address in network for network in kept_out)


def test_argo_cd_follows_the_prod_overlay_on_main() -> None:
    application = yaml.safe_load(APPLICATION.read_text())
    source = application["spec"]["source"]
    assert application["metadata"]["namespace"] == "argocd"
    assert source["path"] == "deploy/k8s/overlays/prod"
    assert source["targetRevision"] == "main"
    assert source["repoURL"] == "https://github.com/gyanendra0/DENIAL-APPEAL-AGENT"
    assert (ROOT / source["path"] / "kustomization.yaml").is_file()


def test_argo_cd_removes_what_left_git_and_undoes_hand_changes() -> None:
    application = yaml.safe_load(APPLICATION.read_text())
    assert application["spec"]["syncPolicy"]["automated"] == {"prune": True, "selfHeal": True}
    assert application["spec"]["destination"]["namespace"] == "denial-appeal-agent"


def test_deleting_the_application_never_deletes_the_app() -> None:
    application = yaml.safe_load(APPLICATION.read_text())
    assert "finalizers" not in application["metadata"]
