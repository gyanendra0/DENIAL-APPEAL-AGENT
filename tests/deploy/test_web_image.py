"""The web image's files keep their promises: pinned bases, a non-root user, a locked frontend
install, the same API paths as the Vite dev server, security headers and screen routing."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DOCKERFILE = ROOT / "deploy" / "web" / "Dockerfile"
CADDYFILE = ROOT / "deploy" / "web" / "Caddyfile"
VITE_CONFIG = ROOT / "frontend" / "vite.config.ts"


def _instructions(name: str) -> list[str]:
    """The arguments of every `name` instruction in the Dockerfile, in order, with the lines a
    trailing backslash continues joined into one."""
    code = "\n".join(
        line for line in DOCKERFILE.read_text().splitlines() if not line.startswith("#")
    )
    lines = code.replace("\\\n", " ").splitlines()
    return [line.split(maxsplit=1)[1] for line in lines if line.startswith(f"{name} ")]


def _caddy_lines() -> list[str]:
    """The Caddyfile's lines without comments or indentation."""
    stripped = (line.strip() for line in CADDYFILE.read_text().splitlines())
    return [line for line in stripped if line and not line.startswith("#")]


def _vite_api_paths() -> set[str]:
    match = re.search(r"const API_PATHS = \[(.*?)\]", VITE_CONFIG.read_text(), re.DOTALL)
    assert match is not None
    return set(re.findall(r'"(/[a-z_-]+)"', match.group(1)))


def _caddy_api_paths() -> list[str]:
    (matcher,) = [line for line in _caddy_lines() if line.startswith("@api path ")]
    return matcher.removeprefix("@api path ").split()


def test_every_base_image_is_pinned_by_digest() -> None:
    bases = _instructions("FROM")
    assert len(bases) == 3
    assert all(re.search(r"@sha256:[0-9a-f]{64}\b", base) for base in bases)


def test_caddy_is_compiled_with_the_fixed_module_versions() -> None:
    runs = " ".join(_instructions("RUN"))
    assert "xcaddy build v2.11.7 " in runs
    assert "github.com/go-jose/go-jose/v4@v4.1.6" in runs
    assert "golang.org/x/net=golang.org/x/net@v0.60.0" in runs
    assert "--from=caddy-build /usr/bin/caddy /usr/bin/caddy" in _instructions("COPY")[-3]


def test_the_compiled_caddy_may_bind_low_ports_without_root() -> None:
    assert "setcap cap_net_bind_service=+ep /usr/bin/caddy" in " ".join(_instructions("RUN"))


def test_curl_is_removed_from_the_final_image() -> None:
    assert "apk del --no-cache curl" in " ".join(_instructions("RUN"))


def test_the_image_runs_as_a_non_root_user() -> None:
    users = _instructions("USER")
    assert users == ["10001"]


def test_the_frontend_is_installed_from_its_lock_file() -> None:
    runs = " ".join(_instructions("RUN"))
    assert "npm ci" in runs
    assert "npm install" not in runs


def test_the_image_has_a_health_check() -> None:
    assert len(_instructions("HEALTHCHECK")) == 1


def test_the_proxy_passes_on_exactly_the_api_paths_the_dev_server_does() -> None:
    caddy_paths = _caddy_api_paths()
    vite_paths = _vite_api_paths()
    assert len(vite_paths) > 5
    # Each path and everything below it, nothing more.
    expected = {form for path in vite_paths for form in (path, f"{path}/*")}
    assert len(caddy_paths) == len(expected)
    assert set(caddy_paths) == expected


def test_api_paths_go_to_the_api_container() -> None:
    assert "reverse_proxy api:8000" in _caddy_lines()


def test_every_response_carries_the_security_headers() -> None:
    lines = _caddy_lines()
    for header in (
        "Strict-Transport-Security ",
        "X-Content-Type-Options nosniff",
        "Referrer-Policy no-referrer",
        "X-Frame-Options DENY",
        "Content-Security-Policy \"default-src 'self';",
    ):
        assert any(line.startswith(header) for line in lines), header


def test_screens_fall_back_to_index_and_the_root_goes_to_the_app() -> None:
    lines = _caddy_lines()
    assert "@screens path /app /app/*" in lines
    assert "rewrite * /index.html" in lines
    assert "redir * /app/" in lines


def test_the_server_name_is_not_sent() -> None:
    assert "-Server" in _caddy_lines()


def test_hashed_assets_may_be_kept_by_the_browser() -> None:
    lines = _caddy_lines()
    assert "handle /assets/* {" in lines
    assert 'header Cache-Control "public, max-age=31536000, immutable"' in lines


def test_any_other_path_is_not_found() -> None:
    lines = _caddy_lines()
    # The bare `handle` comes last in the site block, so it only gets what no route above took.
    assert lines[-4:] == ["handle {", 'respond "Not found" 404', "}", "}"]
