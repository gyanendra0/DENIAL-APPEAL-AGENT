"""Builds the text that is embedded for one evidence chunk.

A chunk on its own can be a short sentence such as "This procedure is covered under
Medicare.", which says nothing without the document it comes from. So the embedded text is
the document title, the section title when the chunk has one, and the chunk text.

`EMBEDDING_INPUT_VERSION` names this recipe. Vectors made from two recipes are not
comparable, so changing the recipe means a new version and a new embedding run. The stored
chunk text and its `text_sha256` are not touched: a citation still points at the chunk text
alone.
"""

EMBEDDING_INPUT_VERSION = "v1"
# The same blank line the chunker puts between the paragraphs of a chunk.
INPUT_PART_SEPARATOR = "\n\n"


def build_embedding_input(document_title: str, section_title: str | None, text: str) -> str:
    """Return the text to embed for a chunk: the titles first, then the chunk text.

    Pure: the same arguments always give the same text. A chunk without a section title
    gets no empty part in its place.
    """
    parts = [document_title] if section_title is None else [document_title, section_title]
    return INPUT_PART_SEPARATOR.join([*parts, text])
