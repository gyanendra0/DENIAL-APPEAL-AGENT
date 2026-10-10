"""HTML-to-paragraphs tests. Every snippet is made up."""

import pytest

from src.ingest.html_paragraphs import html_to_paragraphs


def _texts(html: str) -> list[str]:
    return [paragraph.text for paragraph in html_to_paragraphs(html)]


def test_each_p_tag_gives_one_paragraph() -> None:
    assert _texts("<p>First one.</p><p>Second one.</p>") == ["First one.", "Second one."]


@pytest.mark.parametrize(
    "html",
    [
        "one<br>two",
        "one<br/>two",
        "<div>one</div><div>two</div>",
        "<ul><li>one</li><li>two</li></ul>",
        "<ol><li>one</li><li>two</li></ol>",
        "one<hr>two",
        "<p>one</p>two",
    ],
)
def test_every_break_tag_ends_a_paragraph(html: str) -> None:
    assert _texts(html) == ["one", "two"]


def test_a_line_end_inside_a_paragraph_is_only_a_space() -> None:
    assert _texts("<p>covered when\r\nboth\napply</p>") == ["covered when both apply"]


def test_tabs_and_non_breaking_spaces_become_one_space() -> None:
    assert _texts("<p>\tone&nbsp;&#160; two three </p>") == ["one two three"]


def test_entities_are_decoded() -> None:
    assert _texts("<p>&sect;10.2 &ge; 2 &amp; &#8220;quoted&#8221;</p>") == ["§10.2 ≥ 2 & “quoted”"]


def test_a_bare_ampersand_and_a_raw_less_than_sign_stay_as_text() -> None:
    assert _texts("<p>Medicare & Medicaid, stratify <65 years</p>") == [
        "Medicare & Medicaid, stratify <65 years"
    ]


def test_text_outside_any_tag_is_kept() -> None:
    assert _texts("Loose start.<p>Inside.</p>Loose end.") == [
        "Loose start.",
        "Inside.",
        "Loose end.",
    ]


def test_paragraphs_with_no_text_are_dropped() -> None:
    assert _texts("<p> </p><p>&#160;</p>\r\n<p>kept</p><br><br>") == ["kept"]


@pytest.mark.parametrize("html", ["", "\r\n", "&#160;", "<p> </p>", "<ul><li></li></ul>"])
def test_a_field_with_no_text_gives_no_paragraphs(html: str) -> None:
    assert html_to_paragraphs(html) == []


def test_a_link_keeps_its_words_and_loses_its_address() -> None:
    assert _texts('<p>See <a href="https://example.org/x?id=2">section 10.2</a>.</p>') == [
        "See section 10.2."
    ]


def test_inline_tags_do_not_break_a_paragraph() -> None:
    assert _texts('<p>one <em>two</em> <font color="#FF0000">three</font><sup>4</sup></p>') == [
        "one two three4"
    ]


def test_a_table_gives_one_paragraph_per_row_with_the_cells_separated() -> None:
    html = (
        "<table><tbody><tr><th>Item</th><th>Decision</th></tr>"
        "<tr><td>Device A</td><td>Covered</td></tr></tbody></table>"
    )

    assert _texts(html) == ["Item | Decision", "Device A | Covered"]


def test_an_empty_table_cell_leaves_no_dangling_separator() -> None:
    html = "<table><tr><td></td><td>Device A</td><td> </td><td>Covered</td><td></td></tr></table>"

    assert _texts(html) == ["Device A | Covered"]


def test_a_paragraph_that_is_all_bold_is_a_heading() -> None:
    paragraphs = html_to_paragraphs(
        "<p><strong>B. Covered Indications</strong></p><p>Plain <strong>with bold</strong>.</p>"
    )

    assert [(paragraph.text, paragraph.is_heading) for paragraph in paragraphs] == [
        ("B. Covered Indications", True),
        ("Plain with bold.", False),
    ]


def test_bold_wrapped_around_the_paragraph_tag_is_a_heading_too() -> None:
    paragraphs = html_to_paragraphs("<strong><p> A.  Manipulation</p></strong>\n\n<p>Text.</p>")

    assert [(paragraph.text, paragraph.is_heading) for paragraph in paragraphs] == [
        ("A. Manipulation", True),
        ("Text.", False),
    ]


def test_a_bold_table_row_with_two_cells_is_not_a_heading() -> None:
    paragraphs = html_to_paragraphs(
        "<table><tr><td><strong>Item</strong></td><td><strong>Decision</strong></td></tr></table>"
    )

    assert [(paragraph.text, paragraph.is_heading) for paragraph in paragraphs] == [
        ("Item | Decision", False)
    ]


def test_a_stray_closing_bold_tag_does_not_make_later_text_bold() -> None:
    paragraphs = html_to_paragraphs("</strong><p>Plain.</p><p><strong>Heading</strong></p>")

    assert [paragraph.is_heading for paragraph in paragraphs] == [False, True]


def test_the_same_html_always_gives_the_same_paragraphs() -> None:
    html = "<p><strong>A</strong></p><ul><li>one</li><li>two &amp; three</li></ul>"

    assert html_to_paragraphs(html) == html_to_paragraphs(html)
