"""Loads the versioned drafting prompt.

The prompt is the text file `<prompt folder>/drafting/<version>/appeal_letter.txt`. The
folder comes from settings and is passed in; this module reads no settings itself.
"""

import re
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

# Stored with every model call as `LlmRequest.purpose`, and the name of the prompts' folder.
DRAFTING_PURPOSE = "drafting"
DRAFTING_PROMPT_VERSION = "v3"
# Also keeps a version from pointing outside the prompt folder ("../..").
VERSION_PATTERN = re.compile(r"v[0-9]+")
PROMPT_FILE_NAME = "appeal_letter.txt"


class DraftingPromptError(ValueError):
    """A prompt version is badly formed, or its file is missing or empty."""


class DraftingPrompt(BaseModel):
    """The instruction sent as the system message of a drafting call."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    version: str = Field(min_length=1, max_length=40)
    system: str = Field(min_length=1)


def load_drafting_prompt(prompt_dir: Path, version: str) -> DraftingPrompt:
    """Read the drafting prompt of `version` from `prompt_dir`."""
    if VERSION_PATTERN.fullmatch(version) is None:
        raise DraftingPromptError(f"prompt version {version!r} is not of the form v1, v2, ...")
    path = prompt_dir / DRAFTING_PURPOSE / version / PROMPT_FILE_NAME
    try:
        text = path.read_text(encoding="utf-8").strip()
    except FileNotFoundError as error:
        raise DraftingPromptError(f"prompt file not found: {path}") from error
    if not text:
        raise DraftingPromptError(f"prompt file is empty: {path}")
    return DraftingPrompt(version=version, system=text)
