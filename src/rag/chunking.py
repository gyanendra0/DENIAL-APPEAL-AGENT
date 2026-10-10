"""Cut one document's text into chunks small enough to retrieve and to cite.

The chunker is a pure function: it takes plain paragraphs (the reader has already removed any
markup) and returns the chunks to store. It reads no file, no settings and no database, and the
same text always gives the same chunks.

The rules, `v1`:

1. Paragraphs are packed in reading order, joined by `PARAGRAPH_SEPARATOR`, until the next one
   would take the chunk over `MAX_CHUNK_CHARS`.
2. A chunk never holds text of two sections. `chunk_index` counts from 0 across the sections.
3. A chunk does not end on a heading when the heading fits at the start of the next chunk.
4. A paragraph over the limit is cut at sentence ends; a sentence over the limit is cut at a
   space; a word over the limit is cut at the limit. The pieces are joined back with one space.
5. Chunks do not overlap.

Any change to a rule or a constant here needs a new `CHUNKER_VERSION`.
"""

import hashlib
import re
from collections.abc import Sequence

from pydantic import BaseModel, ConfigDict, Field, field_validator

CHUNKER_VERSION = "v1"
MAX_CHUNK_CHARS = 1200
PARAGRAPH_SEPARATOR = "\n\n"

_SENTENCE_BREAK = re.compile(r"(?<=[.;:?!])\s+")


class Paragraph(BaseModel):
    """One paragraph of plain text. `is_heading` marks a line that only introduces what follows."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    text: str
    is_heading: bool = False

    @field_validator("text")
    @classmethod
    def _text_is_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("a paragraph must hold some text")
        return value


class SectionText(BaseModel):
    """One part of a document: its title, when the source has parts, and its paragraphs."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    title: str | None = Field(min_length=1, max_length=100)
    paragraphs: tuple[Paragraph, ...] = Field(min_length=1)


class Chunk(BaseModel):
    """One chunk, with the columns `evidence_chunks` stores for it."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    chunk_index: int = Field(ge=0)
    section_title: str | None
    text: str = Field(min_length=1, max_length=MAX_CHUNK_CHARS)
    text_sha256: str
    chunker_version: str = CHUNKER_VERSION


def chunk_document(sections: Sequence[SectionText]) -> list[Chunk]:
    """Return the chunks of one document, in reading order. No sections gives no chunks."""
    chunks: list[Chunk] = []
    for section in sections:
        for text in _pack(_within_limit(section.paragraphs)):
            chunks.append(
                Chunk(
                    chunk_index=len(chunks),
                    section_title=section.title,
                    text=text,
                    text_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
                )
            )
    return chunks


def _pack(paragraphs: Sequence[Paragraph]) -> list[str]:
    """Join paragraphs into chunk texts: none over the limit, none ending on a movable heading."""
    texts: list[str] = []
    current: list[Paragraph] = []
    for paragraph in paragraphs:
        if _fits([*current, paragraph]):
            current.append(paragraph)
            continue
        headings = _trailing_headings(current)
        if headings and len(headings) < len(current) and _fits([*headings, paragraph]):
            texts.append(_joined(current[: -len(headings)]))
            current = [*headings, paragraph]
        else:
            texts.append(_joined(current))
            current = [paragraph]
    texts.append(_joined(current))
    return texts


def _fits(paragraphs: Sequence[Paragraph]) -> bool:
    return len(_joined(paragraphs)) <= MAX_CHUNK_CHARS


def _joined(paragraphs: Sequence[Paragraph]) -> str:
    return PARAGRAPH_SEPARATOR.join(paragraph.text for paragraph in paragraphs)


def _trailing_headings(paragraphs: Sequence[Paragraph]) -> list[Paragraph]:
    headings: list[Paragraph] = []
    for paragraph in reversed(paragraphs):
        if not paragraph.is_heading:
            break
        headings.insert(0, paragraph)
    return headings


def _within_limit(paragraphs: Sequence[Paragraph]) -> list[Paragraph]:
    """Replace each paragraph over the limit by its pieces; a cut heading is no longer a heading."""
    result: list[Paragraph] = []
    for paragraph in paragraphs:
        if len(paragraph.text) <= MAX_CHUNK_CHARS:
            result.append(paragraph)
        else:
            result.extend(Paragraph(text=piece) for piece in _cut_paragraph(paragraph.text))
    return result


def _cut_paragraph(text: str) -> list[str]:
    """Cut a long paragraph into pieces within the limit, each as many whole sentences as fit."""
    parts = [part for sentence in _SENTENCE_BREAK.split(text) for part in _cut_sentence(sentence)]
    pieces: list[str] = []
    current = ""
    for part in parts:
        candidate = f"{current} {part}" if current else part
        if len(candidate) <= MAX_CHUNK_CHARS:
            current = candidate
        else:
            pieces.append(current)
            current = part
    pieces.append(current)
    return pieces


def _cut_sentence(sentence: str) -> list[str]:
    """Cut a sentence over the limit at the last space that fits, or at the limit with no space."""
    parts: list[str] = []
    rest = sentence.strip()
    while len(rest) > MAX_CHUNK_CHARS:
        cut = rest.rfind(" ", 0, MAX_CHUNK_CHARS + 1)
        if cut <= 0:
            cut = MAX_CHUNK_CHARS
        parts.append(rest[:cut].rstrip())
        rest = rest[cut:].lstrip()
    if rest:
        parts.append(rest)
    return parts
