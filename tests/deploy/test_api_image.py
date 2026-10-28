"""The API image's files keep their promises: pinned bases, a non-root user, one worker, a
hashed lock file and a build context that lets in only what the image needs."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DOCKERFILE = ROOT / "deploy" / "api" / "Dockerfile"
LOCK_FILE = ROOT / "deploy" / "api" / "requirements.lock"
DOCKERIGNORE = ROOT / ".dockerignore"


def _instructions(name: str) -> list[str]:
    """The arguments of every `name` instruction in the Dockerfile, in order, with the lines a
    trailing backslash continues joined into one."""
    code = "\n".join(
        line for line in DOCKERFILE.read_text().splitlines() if not line.startswith("#")
    )
    lines = code.replace("\\\n", " ").splitlines()
    return [line.split(maxsplit=1)[1] for line in lines if line.startswith(f"{name} ")]


def test_every_base_image_is_pinned_by_digest() -> None:
    bases = _instructions("FROM")
    assert len(bases) == 2
    assert all(re.search(r"@sha256:[0-9a-f]{64}\b", base) for base in bases)


def test_the_image_runs_as_a_non_root_user() -> None:
    users = _instructions("USER")
    assert users == ["10001"]


def test_the_api_starts_with_exactly_one_worker() -> None:
    (command,) = _instructions("CMD")
    assert '"--workers", "1"' in command


def test_the_image_has_a_health_check_on_the_health_route() -> None:
    (check,) = _instructions("HEALTHCHECK")
    assert "/health" in check


def test_every_locked_package_is_pinned_with_a_hash() -> None:
    # Each package starts at an unindented line; its hashes and `# via` notes are indented.
    entries: list[list[str]] = []
    for line in LOCK_FILE.read_text().splitlines():
        if line.startswith("#") or not line.strip():
            continue
        if line.startswith(" "):
            entries[-1].append(line)
        else:
            entries.append([line])
    assert len(entries) > 10
    for first, *rest in entries:
        assert re.fullmatch(r"[a-z0-9._-]+==\S+ \\", first), first
        assert any(line.strip().startswith("--hash=sha256:") for line in rest), first


def test_the_build_context_leaves_everything_out_unless_let_in() -> None:
    lines = [
        line for line in DOCKERIGNORE.read_text().splitlines() if line and not line.startswith("#")
    ]
    assert lines[0] == "*"
    allowed = {line.removeprefix("!") for line in lines if line.startswith("!")}
    assert allowed == {
        "alembic.ini",
        "config/prompts/",
        "deploy/api/requirements.lock",
        "deploy/web/Caddyfile",
        "frontend/",
        "src/",
    }


def test_the_build_context_keeps_the_frontends_installed_and_built_files_out() -> None:
    lines = DOCKERIGNORE.read_text().splitlines()
    # A later line wins, so each exclusion must come after `!frontend/`.
    let_in = lines.index("!frontend/")
    for folder in ("frontend/node_modules/", "frontend/dist/", "frontend/coverage/"):
        assert lines.index(folder) > let_in
