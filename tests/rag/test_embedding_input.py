from src.rag.embedding_input import (
    EMBEDDING_INPUT_VERSION,
    INPUT_PART_SEPARATOR,
    build_embedding_input,
)


def test_the_embedded_text_is_the_two_titles_then_the_chunk_text() -> None:
    text = build_embedding_input("Made-up Determination", "Made-up Section", "Made-up text.")

    assert text == "Made-up Determination\n\nMade-up Section\n\nMade-up text."


def test_a_chunk_without_a_section_title_gets_no_empty_part() -> None:
    text = build_embedding_input("Made-up Determination", None, "Made-up text.")

    assert text == "Made-up Determination\n\nMade-up text."


def test_the_same_chunk_always_gives_the_same_text() -> None:
    arguments = ("Made-up Determination", "Made-up Section", "First paragraph.\n\nSecond.")

    assert build_embedding_input(*arguments) == build_embedding_input(*arguments)


def test_the_chunk_text_is_kept_as_it_is_at_the_end() -> None:
    chunk_text = "  First paragraph.\n\nSecond, with  two spaces. "

    assert build_embedding_input("Title", "Section", chunk_text).endswith(
        INPUT_PART_SEPARATOR + chunk_text
    )


def test_the_recipe_is_version_1() -> None:
    # Changing how the text is built needs a new version: old vectors are not comparable.
    assert EMBEDDING_INPUT_VERSION == "v1"
