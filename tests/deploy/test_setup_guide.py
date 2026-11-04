"""The setup guide stays true to the repository: every repository path it names exists, every link
lands on a real heading, its pinned versions and checksums are the ones the repository pins, and
the README points to it."""

import re
import shutil
import subprocess
from collections import Counter
from pathlib import Path

from src.config.settings import ApiSettings, LlmGatewaySettings, Settings

ROOT = Path(__file__).resolve().parents[2]
GUIDE = ROOT / "docs" / "07-setup-guide.md"
README = ROOT / "README.md"
ADDONS = ROOT / "deploy" / "k8s" / "addons"
MAX_LINE = 100
# Keys `deploy/prod/compose.yml` reads from `.env` itself; the app's settings never see them.
COMPOSE_ONLY_KEYS = {"POSTGRES_PASSWORD", "SITE_ADDRESS", "MODEL_HOST_DIR"}
# Files the guide has the reader make on the machine; they hold secrets and never enter git.
NEVER_IN_GIT = {"deploy/prod/.env"}
# A path inside the repository, wherever it appears: prose, a table or a command.
REPO_PATH = re.compile(
    r"(?<![\w.-])((?:deploy|docs|src|pipelines|tests|config|frontend|\.github)/[\w./-]*)"
)
LINK = re.compile(r"\]\(([^)\s]+)\)")


def _guide() -> str:
    return GUIDE.read_text()


def _outside_code(text: str) -> list[str]:
    """The lines that are not inside a fenced code block."""
    lines: list[str] = []
    in_code = False
    for line in text.splitlines():
        if line.lstrip().startswith("```"):
            in_code = not in_code
            continue
        if not in_code:
            lines.append(line)
    return lines


def _slugs(markdown: Path) -> set[str]:
    """The anchors GitHub gives the headings of a file, numbered as GitHub numbers repeats."""
    seen: Counter[str] = Counter()
    slugs: set[str] = set()
    for line in _outside_code(markdown.read_text()):
        match = re.match(r"#{1,6} (.+)", line)
        if not match:
            continue
        slug = re.sub(r"[^\w\- ]", "", match.group(1).strip().lower()).replace(" ", "-")
        slugs.add(slug if not seen[slug] else f"{slug}-{seen[slug]}")
        seen[slug] += 1
    return slugs


def _addon_pin(folder: str) -> tuple[str, str]:
    """The upstream address and sha256 named in an add-on's kustomization comment."""
    comment = (ADDONS / folder / "kustomization.yaml").read_text().split("apiVersion:")[0]
    url = re.search(r"https://\S+", comment)
    digest = re.search(r"sha256:([0-9a-f]{64})", comment)
    assert url and digest
    return url.group(0), digest.group(1)


def _tracked() -> set[str]:
    """Every file git tracks, and every folder that holds one: what a fresh clone has."""
    git = shutil.which("git")
    assert git is not None
    # Only lists our own repository; nothing from outside reaches the command.
    result = subprocess.run(  # noqa: S603
        [git, "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True
    )
    files = set(result.stdout.splitlines())
    return files | {str(parent) for file in files for parent in Path(file).parents}


def test_every_repository_path_in_the_guide_exists() -> None:
    paths = {match.rstrip(".,/") for match in REPO_PATH.findall(_guide())}
    assert len(paths) >= 10
    tracked = _tracked()
    missing = sorted(path for path in paths - NEVER_IN_GIT if path not in tracked)
    assert not missing


def test_files_the_guide_keeps_out_of_git_are_not_tracked() -> None:
    named = {match.rstrip(".,/") for match in REPO_PATH.findall(_guide())}
    assert NEVER_IN_GIT.issubset(named)
    assert not NEVER_IN_GIT & _tracked()


def test_every_link_in_the_guide_lands_on_a_real_heading() -> None:
    links = [link for link in LINK.findall(_guide()) if not link.startswith("http")]
    assert links
    for link in links:
        file_part, _, anchor = link.partition("#")
        target = (GUIDE.parent / file_part).resolve() if file_part else GUIDE
        assert target.exists(), link
        if anchor:
            assert anchor in _slugs(target), link


def test_guide_installs_the_k3s_version_ci_checks_against() -> None:
    workflow = (ROOT / ".github" / "workflows" / "security.yml").read_text()
    version = re.search(r'KUBERNETES_VERSION: "([\d.]+)"', workflow)
    assert version
    assert re.search(rf"INSTALL_K3S_VERSION=v{re.escape(version.group(1))}\+k3s\d+", _guide())


def test_guide_downloads_each_add_on_from_its_pinned_address_and_checksum() -> None:
    guide = _guide()
    for folder in ("argocd", "sealed-secrets"):
        url, digest = _addon_pin(folder)
        assert url in guide
        assert f"{digest}  {url.rsplit('/', 1)[1]}" in guide


def test_guide_restores_with_the_pinned_database_image() -> None:
    image = re.search(r"image: (pgvector/\S+)", (ROOT / "deploy/k8s/base/db.yaml").read_text())
    assert image
    assert f"DB_IMAGE={image.group(1)}" in _guide()


def test_compose_path_tags_the_images_compose_runs() -> None:
    compose = (ROOT / "deploy" / "prod" / "compose.yml").read_text()
    overlay = (ROOT / "deploy" / "k8s" / "overlays" / "prod" / "kustomization.yaml").read_text()
    tags = set(re.findall(r'docker tag "\$\w+" (\S+)', _guide()))
    assert tags == set(re.findall(r"image: (denial-appeal-agent-\w+)", compose))
    pulled = set(re.findall(r"=(ghcr\.io/\S+/(?:api|web))@", _guide()))
    assert pulled == set(re.findall(r"name: (ghcr\.io/\S+)", overlay))


def test_compose_settings_file_names_only_real_settings() -> None:
    section = _guide().split("### 7.5.3")[1].split("###")[0]
    rows = [line.split("|")[1] for line in section.splitlines() if line.startswith("| `")]
    keys = {key for row in rows for key in re.findall(r"`([A-Z][A-Z_]+)`", row)}
    known = {
        name.upper()
        for settings in (Settings, ApiSettings, LlmGatewaySettings)
        for name in settings.model_fields
    } | COMPOSE_ONLY_KEYS
    assert len(keys) >= 15
    assert keys <= known


def test_guide_prose_wraps_at_100_characters() -> None:
    long_lines = [
        line
        for line in _outside_code(_guide())
        if len(line) > MAX_LINE and not line.startswith("|") and "http" not in line
    ]
    assert not long_lines


def test_readme_lists_the_guide_as_document_seven() -> None:
    assert "| 7 | [Setup Guide](docs/07-setup-guide.md) |" in README.read_text()
