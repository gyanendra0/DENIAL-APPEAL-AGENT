"""The cluster add-ons stay pinned: each one names the exact upstream file by checksum, replaces
every upstream image with a digest, and leaves the downloaded file out of git."""

from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
ADDONS = ROOT / "deploy" / "k8s" / "addons"
ARGOCD_IMAGES = {
    "quay.io/argoproj/argocd": (
        "sha256:8e5cc5bb008df8a09dba439462e58c94c55b7735888d3ebde0c25fc05e4e5335"
    ),
    "ghcr.io/dexidp/dex": (
        "sha256:8499afd690c437f52301efd2b05b2455da5bd2dfc20332cd697dc9937f808462"
    ),
    "public.ecr.aws/docker/library/redis": (
        "sha256:08ad0b1d280850169a790dba1393ff7a90aef951fc19632cf4d3ce4f78e679ba"
    ),
}
SEALED_SECRETS_IMAGES = {
    "docker.io/bitnami/sealed-secrets-controller": (
        "sha256:b1ff382e9300dc9e74991f3177b18cafc06ce43b5c21349c86adbdd1d1c177d3"
    ),
}
# (folder, upstream file, its address, its checksum, the images it must pin)
PINS = [
    (
        "argocd",
        "install.yaml",
        "https://raw.githubusercontent.com/argoproj/argo-cd/v3.5.6/manifests/install.yaml",
        "sha256:846c644bc81ce5557dd9048757a9c7353378f25c5eb811c77d32a9e907823599",
        ARGOCD_IMAGES,
    ),
    (
        "sealed-secrets",
        "controller.yaml",
        "https://github.com/bitnami-labs/sealed-secrets/releases/download/v0.40.0/controller.yaml",
        "sha256:ac8aabccdd9110430d117501705818115b2ab0993700a9c118d4d42cf4015a17",
        SEALED_SECRETS_IMAGES,
    ),
]


def _kustomization(folder: str) -> dict[str, Any]:
    loaded: dict[str, Any] = yaml.safe_load((ADDONS / folder / "kustomization.yaml").read_text())
    return loaded


@pytest.mark.parametrize(("folder", "upstream", "url", "checksum", "images"), PINS)
def test_addon_applies_only_the_pinned_upstream_file(
    folder: str, upstream: str, url: str, checksum: str, images: dict[str, str]
) -> None:
    text = (ADDONS / folder / "kustomization.yaml").read_text()

    assert _kustomization(folder)["resources"] == [upstream]
    assert url in text
    assert checksum in text


@pytest.mark.parametrize(("folder", "upstream", "url", "checksum", "images"), PINS)
def test_addon_pins_every_image_by_digest_alone(
    folder: str, upstream: str, url: str, checksum: str, images: dict[str, str]
) -> None:
    entries = _kustomization(folder)["images"]

    assert {entry["name"]: entry["digest"] for entry in entries} == images
    assert all(set(entry) == {"name", "digest"} for entry in entries)


@pytest.mark.parametrize(("folder", "upstream", "url", "checksum", "images"), PINS)
def test_downloaded_addon_file_is_left_out_of_git(
    folder: str, upstream: str, url: str, checksum: str, images: dict[str, str]
) -> None:
    ignored = (ROOT / ".gitignore").read_text().splitlines()

    assert f"deploy/k8s/addons/{folder}/{upstream}" in ignored


def test_argocd_installs_into_its_own_namespace() -> None:
    assert _kustomization("argocd")["namespace"] == "argocd"
