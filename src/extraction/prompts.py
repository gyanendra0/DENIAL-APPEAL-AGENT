"""Loads the versioned extraction prompts.

A prompt is a text file `<prompt folder>/extraction/<version>/<document type>.txt`. The
folder comes from settings and is passed in; this module reads no settings itself.
"""

import re
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from src.db.models import DocumentType

# Stored with every model call as `LlmRequest.purpose`, and the name of the prompts' folder.
EXTRACTION_PURPOSE = "extraction"
EXTRACTION_PROMPT_VERSION = "v1"
# Also keeps a version from pointing outside the prompt folder ("../..").
VERSION_PATTERN = re.compile(r"v[0-9]+")
PROMPT_FILE_SUFFIX = ".txt"


class PromptError(ValueError):
    """A prompt version is badly formed, or its file is missing or empty."""


class ExtractionPrompt(BaseModel):
    """The instruction sent as the system message for one document type."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    version: str = Field(min_length=1, max_length=40)
    document_type: DocumentType
    system: str = Field(min_length=1)


def load_extraction_prompt(
    prompt_dir: Path, version: str, document_type: DocumentType
) -> ExtractionPrompt:
    """Read the extraction prompt of `version` for `document_type` from `prompt_dir`."""
    if VERSION_PATTERN.fullmatch(version) is None:
        raise PromptError(f"prompt version {version!r} is not of the form v1, v2, ...")
    path = prompt_dir / EXTRACTION_PURPOSE / version / f"{document_type.value}{PROMPT_FILE_SUFFIX}"
    try:
        text = path.read_text(encoding="utf-8").strip()
    except FileNotFoundError as error:
        raise PromptError(f"prompt file not found: {path}") from error
    if not text:
        raise PromptError(f"prompt file is empty: {path}")
    return ExtractionPrompt(version=version, document_type=document_type, system=text)
