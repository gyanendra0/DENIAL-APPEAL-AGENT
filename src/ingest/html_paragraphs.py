"""Turn one HTML field of a policy source into plain paragraphs for the chunker.

The policy text is published as HTML fragments, sometimes badly formed. The paragraph breaks
are the tags, not the line ends: a line end inside a paragraph is only a soft wrap. This
module keeps the words and the paragraph breaks and drops everything else:

- A paragraph ends at every tag of `BREAK_TAGS`.
- A table gives one paragraph per row, its filled cells joined by `CELL_SEPARATOR`.
- Entities are decoded, and every run of whitespace (non-breaking spaces, tabs and line ends
  too) becomes one space. A paragraph with no text is dropped.
- A paragraph whose whole text is bold is marked as a heading.
- A link keeps its visible words and loses its address. List numbers that the source keeps in
  attributes (`<ol type="a">`) are lost.
"""

from html.parser import HTMLParser

from src.rag.chunking import Paragraph

BREAK_TAGS = frozenset({"p", "div", "li", "ul", "ol", "table", "tr", "hr", "br"})
CELL_TAGS = frozenset({"td", "th"})
BOLD_TAGS = frozenset({"strong", "b"})
CELL_SEPARATOR = " | "


def html_to_paragraphs(html: str) -> list[Paragraph]:
    """Return the paragraphs of one HTML field, in reading order. No text gives an empty list."""
    collector = _ParagraphCollector()
    collector.feed(html)
    collector.close()
    collector.end_paragraph()
    return collector.paragraphs


class _ParagraphCollector(HTMLParser):
    """Collects the text between two breaks, with whether each piece of it was bold."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.paragraphs: list[Paragraph] = []
        self._pieces: list[tuple[str, bool]] = []
        self._bold_depth = 0
        self._cell_pending = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in BREAK_TAGS:
            self.end_paragraph()
        elif tag in CELL_TAGS:
            # The separator is only added once the cell turns out to hold text.
            self._cell_pending = self._has_text()
        elif tag in BOLD_TAGS:
            self._bold_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in BREAK_TAGS:
            self.end_paragraph()
        elif tag in BOLD_TAGS:
            self._bold_depth = max(0, self._bold_depth - 1)

    def handle_data(self, data: str) -> None:
        if not data.strip():
            self._pieces.append((data, False))
            return
        if self._cell_pending:
            self._pieces.append((CELL_SEPARATOR, False))
            self._cell_pending = False
        self._pieces.append((data, self._bold_depth > 0))

    def end_paragraph(self) -> None:
        """Store the text collected since the last break, if there is any."""
        text = " ".join("".join(piece for piece, _ in self._pieces).split())
        if text:
            is_heading = all(bold for piece, bold in self._pieces if piece.strip())
            self.paragraphs.append(Paragraph(text=text, is_heading=is_heading))
        self._pieces = []
        self._cell_pending = False

    def _has_text(self) -> bool:
        return any(piece.strip() for piece, _ in self._pieces)
