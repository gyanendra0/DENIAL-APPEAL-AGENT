"""The deploy pull request changes the digest lines and nothing else, or refuses."""

from pathlib import Path

import pytest

from pipelines.set_image_digests import EXIT_BAD_INPUT, EXIT_OK, main

API = "ghcr.io/example/api"
WEB = "ghcr.io/example/web"
OLD = "sha256:" + "0" * 64
NEW_API = "sha256:" + "a" * 64
NEW_WEB = "sha256:" + "b" * 64
FILE_TEXT = f"""# A comment that stays.
resources:
  - ../../base
images:
  - name: {API}
    digest: {OLD}
  - name: {WEB}
    digest: {OLD}
"""


@pytest.fixture
def kustomization(tmp_path: Path) -> Path:
    path = tmp_path / "kustomization.yaml"
    path.write_text(FILE_TEXT)
    return path


def _run(path: Path, *images: str) -> int:
    arguments = ["--file", str(path)]
    for image in images:
        arguments += ["--image", image]
    return main(arguments)


def test_both_digests_change_and_every_other_line_stays(kustomization: Path) -> None:
    assert _run(kustomization, f"{API}={NEW_API}", f"{WEB}={NEW_WEB}") == EXIT_OK
    assert kustomization.read_text() == FILE_TEXT.replace(
        f"{API}\n    digest: {OLD}", f"{API}\n    digest: {NEW_API}"
    ).replace(f"{WEB}\n    digest: {OLD}", f"{WEB}\n    digest: {NEW_WEB}")


def test_the_same_digests_leave_the_file_exactly_as_it_was(kustomization: Path) -> None:
    assert _run(kustomization, f"{API}={OLD}", f"{WEB}={OLD}") == EXIT_OK
    assert kustomization.read_text() == FILE_TEXT


@pytest.mark.parametrize(
    "images",
    [
        pytest.param([f"{API}={NEW_API}"], id="an image in the file is not given"),
        pytest.param(
            [f"{API}={NEW_API}", f"{WEB}={NEW_WEB}", f"ghcr.io/example/other={NEW_WEB}"],
            id="a given image is not in the file",
        ),
        pytest.param([f"{API}=sha256:abc", f"{WEB}={NEW_WEB}"], id="a short digest"),
        pytest.param([f"{API}=latest", f"{WEB}={NEW_WEB}"], id="a tag and not a digest"),
        pytest.param([f"{API}={NEW_API}".upper(), f"{WEB}={NEW_WEB}"], id="capital letters"),
        pytest.param([API, f"{WEB}={NEW_WEB}"], id="no digest at all"),
    ],
)
def test_bad_images_are_refused_and_nothing_is_written(
    kustomization: Path, images: list[str]
) -> None:
    assert _run(kustomization, *images) == EXIT_BAD_INPUT
    assert kustomization.read_text() == FILE_TEXT


def test_an_image_with_no_digest_line_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "kustomization.yaml"
    text = f"images:\n  - name: {API}\n    newTag: v1\n"
    path.write_text(text)
    assert _run(path, f"{API}={NEW_API}") == EXIT_BAD_INPUT
    assert path.read_text() == text


def test_an_image_on_the_last_line_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "kustomization.yaml"
    path.write_text(f"images:\n  - name: {API}\n")
    assert _run(path, f"{API}={NEW_API}") == EXIT_BAD_INPUT


@pytest.mark.parametrize("other", ["before", "after"])
def test_a_name_line_in_another_block_is_left_alone(tmp_path: Path, other: str) -> None:
    block = "configMapGenerator:\n  - name: app-settings\n    literals:\n      - A=1\n"
    images = f"images:\n  - name: {API}\n    digest: {OLD}\n"
    text = block + images if other == "before" else images + block
    path = tmp_path / "kustomization.yaml"
    path.write_text(text)
    assert _run(path, f"{API}={NEW_API}") == EXIT_OK
    assert path.read_text() == text.replace(OLD, NEW_API)


def test_a_file_with_no_images_block_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "kustomization.yaml"
    path.write_text(f"patches:\n  - name: {API}\n    digest: {OLD}\n")
    assert _run(path, f"{API}={NEW_API}") == EXIT_BAD_INPUT


def test_a_missing_file_is_refused(tmp_path: Path) -> None:
    assert _run(tmp_path / "missing.yaml", f"{API}={NEW_API}") == EXIT_BAD_INPUT
