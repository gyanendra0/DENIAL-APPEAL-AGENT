import hashlib

import pytest
from pydantic import ValidationError

from src.rag.chunking import (
    CHUNKER_VERSION,
    MAX_CHUNK_CHARS,
    PARAGRAPH_SEPARATOR,
    Chunk,
    Paragraph,
    SectionText,
    chunk_document,
)

TITLE = "Made-up Section"
PINNED_TEXT = "The pump is covered.\n\nThe strap is not covered."
PINNED_SHA256 = "27d791174523384b6eb71116218585645ee13e09051afe7ba7291a85dff56de7"


def words(length: int) -> str:
    """Made-up text of exactly `length` characters: five-letter words, no sentence end."""
    text = ("lorem " * (length // 6 + 1))[:length]
    return text[:-1] + "m" if text.endswith(" ") else text


def sentence(length: int) -> str:
    return words(length - 1) + "."


def section(*paragraphs: str | Paragraph, title: str | None = TITLE) -> SectionText:
    return SectionText(
        title=title,
        paragraphs=tuple(
            item if isinstance(item, Paragraph) else Paragraph(text=item) for item in paragraphs
        ),
    )


def heading(text: str) -> Paragraph:
    return Paragraph(text=text, is_heading=True)


def texts(chunks: list[Chunk]) -> list[str]:
    return [chunk.text for chunk in chunks]


def test_short_paragraphs_are_packed_into_one_chunk_with_a_blank_line_between() -> None:
    chunks = chunk_document([section("The pump is covered.", "The strap is not covered.")])

    assert texts(chunks) == [PINNED_TEXT]
    assert PARAGRAPH_SEPARATOR == "\n\n"


def test_a_chunk_of_exactly_the_limit_is_allowed() -> None:
    first, second = words(700), words(MAX_CHUNK_CHARS - 700 - len(PARAGRAPH_SEPARATOR))

    chunks = chunk_document([section(first, second)])

    assert len(chunks) == 1
    assert len(chunks[0].text) == MAX_CHUNK_CHARS


def test_a_new_chunk_starts_when_the_next_paragraph_would_pass_the_limit() -> None:
    first, second = words(700), words(MAX_CHUNK_CHARS - 700 - len(PARAGRAPH_SEPARATOR) + 1)

    assert texts(chunk_document([section(first, second)])) == [first, second]


@pytest.mark.parametrize(
    "paragraphs",
    [
        [words(300)] * 12,
        [words(1200), words(1201), words(1199)],
        [" ".join([sentence(250)] * 20)],
        [words(40), " ".join([sentence(543)] * 6), words(40)],
        ["x" * 5000, words(10)],
        [sentence(1300) + " " + sentence(20)],
    ],
    ids=[
        "many medium paragraphs",
        "around the limit",
        "one paragraph of many sentences",
        "a long paragraph between short ones",
        "a word longer than the limit",
        "a sentence longer than the limit",
    ],
)
def test_no_chunk_is_over_the_limit_and_no_text_is_lost(paragraphs: list[str]) -> None:
    chunks = chunk_document([section(*paragraphs)])

    assert all(0 < len(chunk.text) <= MAX_CHUNK_CHARS for chunk in chunks)
    before = "".join("".join(paragraphs).split())
    after = "".join("".join(texts(chunks)).split())
    assert after == before


def test_a_paragraph_over_the_limit_is_cut_at_sentence_ends() -> None:
    paragraph = " ".join([sentence(400)] * 5)

    chunks = chunk_document([section(paragraph)])

    assert [len(chunk.text) for chunk in chunks] == [801, 801, 400]
    assert all(chunk.text.endswith(".") for chunk in chunks)
    assert " ".join(texts(chunks)) == paragraph


@pytest.mark.parametrize("mark", [".", ";", ":", "?", "!"])
def test_each_sentence_end_mark_is_a_place_to_cut(mark: str) -> None:
    first, second = words(799) + mark, words(800)

    assert texts(chunk_document([section(f"{first} {second}")])) == [first, second]


def test_a_sentence_over_the_limit_is_cut_at_a_space_and_no_word_is_broken() -> None:
    paragraph = words(3000)

    chunks = chunk_document([section(paragraph)])

    assert len(chunks) == 3
    assert " ".join(texts(chunks)) == paragraph


def test_a_word_over_the_limit_is_cut_at_the_limit() -> None:
    chunks = chunk_document([section("x" * 2500)])

    assert [len(chunk.text) for chunk in chunks] == [MAX_CHUNK_CHARS, MAX_CHUNK_CHARS, 100]


def test_a_chunk_never_mixes_two_sections_and_carries_its_sections_title() -> None:
    chunks = chunk_document(
        [section("First part.", title="Part One"), section("Second part.", title="Part Two")]
    )

    assert [(chunk.section_title, chunk.text) for chunk in chunks] == [
        ("Part One", "First part."),
        ("Part Two", "Second part."),
    ]


def test_a_section_without_a_title_gives_chunks_without_one() -> None:
    assert chunk_document([section("Some text.", title=None)])[0].section_title is None


def test_the_chunk_index_counts_from_zero_across_sections() -> None:
    chunks = chunk_document([section(words(900), words(900)), section(words(900), words(900))])

    assert [chunk.chunk_index for chunk in chunks] == [0, 1, 2, 3]


def test_a_heading_at_the_end_of_a_chunk_moves_to_the_start_of_the_next() -> None:
    body, title, after = words(1000), heading("B. Made-up Covered Uses"), words(500)

    chunks = chunk_document([section(body, title, after)])

    assert texts(chunks) == [body, f"{title.text}{PARAGRAPH_SEPARATOR}{after}"]


def test_the_same_line_not_marked_as_a_heading_stays_where_it_fits() -> None:
    body, line, after = words(1000), "B. Made-up Covered Uses", words(500)

    chunks = chunk_document([section(body, line, after)])

    assert texts(chunks) == [f"{body}{PARAGRAPH_SEPARATOR}{line}", after]


def test_two_headings_in_a_row_move_together() -> None:
    body, after = words(1000), words(500)

    chunks = chunk_document([section(body, heading("Part B"), heading("1. Uses"), after)])

    assert texts(chunks) == [body, PARAGRAPH_SEPARATOR.join(["Part B", "1. Uses", after])]


def test_a_heading_stays_when_it_does_not_fit_with_the_next_paragraph() -> None:
    body, title, after = words(600), heading("B. Made-up Covered Uses"), words(MAX_CHUNK_CHARS - 5)

    chunks = chunk_document([section(body, title, after)])

    assert texts(chunks) == [f"{body}{PARAGRAPH_SEPARATOR}{title.text}", after]


def test_headings_with_no_text_before_them_stay_together_as_one_chunk() -> None:
    after = words(MAX_CHUNK_CHARS - 5)

    chunks = chunk_document([section(heading("Part B"), heading("1. Uses"), after)])

    assert texts(chunks) == [f"Part B{PARAGRAPH_SEPARATOR}1. Uses", after]


def test_a_long_paragraph_that_ends_with_a_space_gives_no_empty_chunk() -> None:
    first, second = sentence(800), sentence(800)

    assert texts(chunk_document([section(f"{first} {second} ")])) == [first, second]


def test_a_heading_at_the_end_of_a_section_stays_in_that_section() -> None:
    chunks = chunk_document(
        [section("Some text.", heading("Left-over heading")), section("Next part.")]
    )

    assert texts(chunks) == [f"Some text.{PARAGRAPH_SEPARATOR}Left-over heading", "Next part."]


def test_the_same_text_always_gives_the_same_chunks() -> None:
    sections = [section(words(900), heading("A heading"), " ".join([sentence(300)] * 9))]

    assert chunk_document(sections) == chunk_document(sections)


def test_the_hash_is_the_sha256_of_the_chunk_text_and_the_rules_are_pinned() -> None:
    chunks = chunk_document(
        [
            section("The pump is covered.", "The strap is not covered."),
            section("Covered under § 1862(a)(1)(A) as “reasonable” care."),
        ]
    )

    assert chunks[0].text_sha256 == PINNED_SHA256
    assert "§" in chunks[1].text and "“" in chunks[1].text
    for chunk in chunks:
        assert chunk.text_sha256 == hashlib.sha256(chunk.text.encode("utf-8")).hexdigest()
        assert chunk.chunker_version == CHUNKER_VERSION == "v1"


def test_a_document_with_no_sections_gives_no_chunks() -> None:
    assert chunk_document([]) == []


@pytest.mark.parametrize("text", ["", "   ", "\n\t "], ids=["empty", "spaces", "line breaks"])
def test_a_paragraph_without_text_is_refused(text: str) -> None:
    with pytest.raises(ValidationError, match="a paragraph must hold some text"):
        Paragraph(text=text)


def test_a_section_without_paragraphs_is_refused() -> None:
    with pytest.raises(ValidationError, match="paragraphs"):
        SectionText(title=TITLE, paragraphs=())


def test_a_section_title_too_long_for_the_table_is_refused() -> None:
    with pytest.raises(ValidationError, match="title"):
        SectionText(title="t" * 101, paragraphs=(Paragraph(text="Some text."),))
