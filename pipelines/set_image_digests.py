"""Write new image digests into a Kustomize file, for the deploy pull request.

Usage:
    python3 -m pipelines.set_image_digests --file <kustomization.yaml> \
        --image <name>=<sha256:digest> --image <name>=<sha256:digest>

Only the `digest:` line under each named image changes, so every other line and comment stays.
Only the `images:` block is read: other blocks of the file have `- name:` lines of their own.
Every image in the file must be given and every image given must be in the file: a deploy moves
all the images together, so the migration and the API are always the same version.

Runs in the workflow with no install, so it uses the standard library only.

Exit codes: 0 the digests were written, 2 bad arguments or a file that cannot be used (nothing
is written).
"""

import argparse
import re
import sys
from collections.abc import Sequence
from pathlib import Path

EXIT_OK = 0
EXIT_BAD_INPUT = 2

DIGEST_PATTERN = re.compile(r"sha256:[0-9a-f]{64}")
TOP_LEVEL_KEY_PATTERN = re.compile(r"([A-Za-z]\w*):.*")
NAME_LINE_PATTERN = re.compile(r"\s*-\s*name:\s*(\S+)\s*")
DIGEST_LINE_PATTERN = re.compile(r"(\s*)digest:\s*\S+\s*")


class DigestFileError(Exception):
    """The file or the images given cannot be used. The text names what is wrong."""


def parse_image(text: str) -> tuple[str, str]:
    """Split `<name>=<digest>`, refusing a digest that is not a full sha256."""
    name, separator, digest = text.partition("=")
    if not (name and separator):
        raise DigestFileError(f"expected <name>=<digest>: {text}")
    if not DIGEST_PATTERN.fullmatch(digest):
        raise DigestFileError(f"not a sha256 digest: {name}")
    return name, digest


def set_digests(text: str, digests: dict[str, str]) -> str:
    """Return the file text with each image's digest line replaced."""
    lines = text.splitlines(keepends=True)
    found: set[str] = set()
    in_images = False
    for index, line in enumerate(lines):
        top_level_key = TOP_LEVEL_KEY_PATTERN.fullmatch(line.rstrip("\n"))
        if top_level_key is not None:
            in_images = top_level_key.group(1) == "images"
            continue
        name_line = NAME_LINE_PATTERN.fullmatch(line.rstrip("\n"))
        if name_line is None or not in_images:
            continue
        name = name_line.group(1)
        if name not in digests:
            raise DigestFileError(f"no digest given for the image in the file: {name}")
        digest_line = (
            DIGEST_LINE_PATTERN.fullmatch(lines[index + 1].rstrip("\n"))
            if index + 1 < len(lines)
            else None
        )
        if digest_line is None:
            raise DigestFileError(f"no digest line under the image: {name}")
        lines[index + 1] = f"{digest_line.group(1)}digest: {digests[name]}\n"
        found.add(name)
    missing = sorted(set(digests) - found)
    if missing:
        raise DigestFileError(f"image not in the file: {', '.join(missing)}")
    return "".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Write new image digests into a Kustomize file.")
    parser.add_argument("--file", type=Path, required=True)
    parser.add_argument("--image", action="append", required=True, metavar="NAME=DIGEST")
    arguments = parser.parse_args(argv)
    try:
        digests = dict(parse_image(image) for image in arguments.image)
        text = set_digests(arguments.file.read_text(), digests)
    except (DigestFileError, OSError) as error:
        print(f"cannot set the digests: {error}", file=sys.stderr)
        return EXIT_BAD_INPUT
    arguments.file.write_text(text)
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
