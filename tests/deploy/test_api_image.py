"""The API image's files keep their promises: pinned bases, a non-root user, one worker, a
hashed lock file and a build context that lets in only what the image needs."""

import re
import tomllib
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
DOCKERFILE = ROOT / "deploy" / "api" / "Dockerfile"
LOCK_FILE = ROOT / "deploy" / "api" / "requirements.txt"
PYPROJECT = ROOT / "pyproject.toml"
PYTHON_VERSION = ROOT / "deploy" / "api" / ".python-version"
DOCKERIGNORE = ROOT / ".dockerignore"
CHECKS_WORKFLOW = ROOT / ".github" / "workflows" / "checks.yml"


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


def test_the_api_writes_no_access_log() -> None:
    # The access log would record the Google callback's `?code=...&state=...`.
    (command,) = _instructions("CMD")
    assert '"--no-access-log"' in command


def test_the_api_reads_the_client_ip_from_the_proxy_header() -> None:
    # The address to trust comes from FORWARDED_ALLOW_IPS; a flag here would override it.
    (command,) = _instructions("CMD")
    assert '"--proxy-headers"' in command
    assert "--forwarded-allow-ips" not in command


def test_the_image_trusts_no_proxy_by_default() -> None:
    envs = " ".join(_instructions("ENV"))
    (trusted,) = re.findall(r"\bFORWARDED_ALLOW_IPS=(\S+)", envs)
    assert trusted == "127.0.0.1"


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


def _version_numbers(version: str) -> tuple[int, ...]:
    """A version as numbers, so 6.9 sorts below 6.21; as text it would sort above."""
    return tuple(int(part) for part in version.split("."))


def test_the_locked_pypdf_meets_the_floor_in_pyproject() -> None:
    # The floor keeps out the versions with known denial-of-service bugs; the image installs the
    # lock, not pyproject.toml, so a lock left behind would ship them.
    dependencies = tomllib.loads(PYPROJECT.read_text())["project"]["dependencies"]
    floor = next(item.removeprefix("pypdf>=") for item in dependencies if "pypdf" in item)
    locked = re.search(r"^pypdf==(\S+) \\$", LOCK_FILE.read_text(), re.MULTILINE)
    assert locked is not None
    assert _version_numbers(locked.group(1)) >= _version_numbers(floor)


def test_the_image_build_fails_on_a_lock_with_a_missing_package() -> None:
    # `--no-deps` checks nothing, so `pip check` must follow the install.
    runs = _instructions("RUN")
    install = next(i for i, run in enumerate(runs) if "--no-deps" in run)
    assert runs[install + 1] == "/opt/venv/bin/pip check"


def _lock_test_commands() -> list[str]:
    workflow = yaml.safe_load(CHECKS_WORKFLOW.read_text())
    return [step["run"] for step in workflow["jobs"]["lock-tests"]["steps"] if "run" in step]


def test_ci_installs_the_lock_and_checks_it() -> None:
    commands = "\n".join(_lock_test_commands())
    assert "--require-hashes --no-deps -r deploy/api/requirements.txt" in commands
    assert '"$RUNNER_TEMP/lock/bin/pip" check' in commands


def test_ci_runs_the_tests_on_the_locked_versions() -> None:
    commands = "\n".join(_lock_test_commands())
    assert '-e ".[dev]" -c "$RUNNER_TEMP/locked.txt"' in commands
    assert _lock_test_commands()[-1] == '"$RUNNER_TEMP/lock/bin/pytest"'


def test_dependabot_is_told_the_python_the_lock_is_for() -> None:
    assert PYTHON_VERSION.read_text().strip() == "3.11"


def test_the_build_context_leaves_everything_out_unless_let_in() -> None:
    lines = [
        line for line in DOCKERIGNORE.read_text().splitlines() if line and not line.startswith("#")
    ]
    assert lines[0] == "*"
    allowed = {line.removeprefix("!") for line in lines if line.startswith("!")}
    assert allowed == {
        "alembic.ini",
        "config/prompts/",
        "deploy/api/requirements.txt",
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


def test_the_build_context_keeps_env_files_out_of_every_let_in_folder() -> None:
    lines = DOCKERIGNORE.read_text().splitlines()
    # A later line wins, so it must come after every `!` line.
    let_in = max(i for i, line in enumerate(lines) if line.startswith("!"))
    assert lines.index("**/.env*") > let_in
