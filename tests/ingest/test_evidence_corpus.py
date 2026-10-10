"""Evidence corpus reader tests. The fixture has five made-up determinations in csv rows 2 to 6.

Row 2 has both policy columns, a heading, a list and text in the columns that are never
stored. Row 3 is a table. Row 4 is a retired notice. Row 5 has a blank second policy column
and a double space in its title. Row 6 has loose text and a line break.
"""

import hashlib
import io
import logging
from datetime import date
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src.db.models import EvidenceChunk, EvidenceDocument, EvidenceSource
from src.ingest.evidence_corpus import (
    CSV_NAME,
    EXPECTED_HEADER,
    NESTED_ARCHIVE_NAME,
    EvidenceCorpusBatch,
    read_evidence_corpus,
    replace_evidence_corpus,
)
from src.ingest.marketplace_denials import BatchRejectedError
from src.rag.chunking import CHUNKER_VERSION, MAX_CHUNK_CHARS
from tests.ingest.helpers import ncd_csv, ncd_zip

DESCRIPTION_TITLE = "Item/Service Description"
INDICATIONS_TITLE = "Indications and Limitations of Coverage"
NEVER_STORED_MARKERS = (
    "CROSS-REFERENCE-MARKER",
    "OTHER-TEXT-MARKER",
    "REVISION-HISTORY-MARKER",
    "KEYWORD-MARKER",
    "RETIRED-NOTICE-MARKER",
    "Z9999",
    "example.org",
)
NEVER_STORED_HEADERS = ("xref_txt", "othr_txt", "rev_hstry", "ncd_keyword", "trnsmtl_url")


def _rejected(path: Path) -> list[str]:
    with pytest.raises(BatchRejectedError) as excinfo:
        read_evidence_corpus(path)
    return excinfo.value.problems


def _stored_counts(session: Session) -> tuple[int, int]:
    """(document rows, chunk rows)."""
    documents = session.scalar(select(func.count()).select_from(EvidenceDocument)) or 0
    chunks = session.scalar(select(func.count()).select_from(EvidenceChunk)) or 0
    return documents, chunks


def _stored_hashes(session: Session) -> list[tuple[str, int, str]]:
    rows = session.execute(
        select(
            EvidenceDocument.source_document_id,
            EvidenceChunk.chunk_index,
            EvidenceChunk.text_sha256,
        )
        .join(EvidenceChunk, EvidenceChunk.evidence_document_id == EvidenceDocument.id)
        .order_by(EvidenceDocument.source_document_id, EvidenceChunk.chunk_index)
    )
    return [(row[0], row[1], row[2]) for row in rows]


def test_returns_one_document_per_determination_that_is_not_retired(tmp_path: Path) -> None:
    batch = read_evidence_corpus(ncd_zip(tmp_path))

    assert [entry.document.source_document_id for entry in batch.entries] == ["1", "2", "5", "7"]
    assert [len(entry.chunks) for entry in batch.entries] == [2, 1, 1, 2]
    assert batch.chunk_count == 6
    assert batch.retired_skipped == 1


def test_reads_every_field_of_a_document(tmp_path: Path) -> None:
    document = read_evidence_corpus(ncd_zip(tmp_path)).entries[0].document

    assert document.model_dump() == {
        "source": EvidenceSource.CMS_NCD,
        "source_document_id": "1",
        "section_number": "10.1",
        "title": "Example Blanket Therapy",
        "version_number": 3,
        "effective_date": date(2024, 5, 27),
        "source_file_date": date(2026, 10, 5),
    }


def test_the_file_date_is_the_one_the_zip_records_for_the_csv(tmp_path: Path) -> None:
    batch = read_evidence_corpus(ncd_zip(tmp_path, file_date=(2025, 1, 31)))

    assert {entry.document.source_file_date for entry in batch.entries} == {date(2025, 1, 31)}


def test_each_policy_column_is_its_own_section_in_reading_order(tmp_path: Path) -> None:
    chunks = read_evidence_corpus(ncd_zip(tmp_path)).entries[0].chunks

    assert [(chunk.chunk_index, chunk.section_title, chunk.text) for chunk in chunks] == [
        (
            0,
            DESCRIPTION_TITLE,
            "Blanket therapy is a made-up service that exists only in tests.",
        ),
        (
            1,
            INDICATIONS_TITLE,
            "B. Nationally Covered Indications\n\n"
            "The made-up service is covered when both of these apply:\n\n"
            "the first made-up condition\n\n"
            "the second made-up condition",
        ),
    ]


def test_every_chunk_has_the_hash_of_its_text_and_the_chunker_version(tmp_path: Path) -> None:
    batch = read_evidence_corpus(ncd_zip(tmp_path))

    for entry in batch.entries:
        for chunk in entry.chunks:
            assert chunk.text_sha256 == hashlib.sha256(chunk.text.encode("utf-8")).hexdigest()
            assert chunk.chunker_version == CHUNKER_VERSION


def test_a_table_row_becomes_one_paragraph(tmp_path: Path) -> None:
    (chunk,) = read_evidence_corpus(ncd_zip(tmp_path)).entries[1].chunks

    assert chunk.text == (
        "Item | Decision\n\nMade-up device A | Covered\n\nMade-up device B | Deny & review"
    )
    assert chunk.section_title == INDICATIONS_TITLE


def test_a_blank_policy_column_gives_no_section(tmp_path: Path) -> None:
    entry = read_evidence_corpus(ncd_zip(tmp_path)).entries[2]

    assert [chunk.section_title for chunk in entry.chunks] == [DESCRIPTION_TITLE]
    assert entry.document.title == "Example Description Only"  # the double space is gone


def test_loose_text_and_line_breaks_are_kept_as_paragraphs(tmp_path: Path) -> None:
    chunks = read_evidence_corpus(ncd_zip(tmp_path)).entries[3].chunks

    assert [chunk.text for chunk in chunks] == [
        "A. General\n\nMade-up imaging ≥ 2 times a year.",
        "Loose text outside any tag.\n\nA second line after a break.",
    ]


def test_carriage_returns_and_tabs_in_a_field_do_not_reach_the_text(tmp_path: Path) -> None:
    edits = {(3, "indctn_lmtn"): "<p>one\r\n\ttwo</p>\r\n\r\n<p>three</p>"}

    (chunk,) = read_evidence_corpus(ncd_zip(tmp_path, edits)).entries[1].chunks

    assert chunk.text == "one two\n\nthree"


def test_a_paragraph_over_the_limit_is_cut_and_no_chunk_is_too_long(tmp_path: Path) -> None:
    sentence = "This made-up sentence is repeated to fill the paragraph. "
    edits = {(3, "indctn_lmtn"): f"<p>{sentence * 60}</p>"}

    chunks = read_evidence_corpus(ncd_zip(tmp_path, edits)).entries[1].chunks

    assert len(chunks) == 3
    assert all(len(chunk.text) <= MAX_CHUNK_CHARS for chunk in chunks)
    assert all(chunk.text.endswith("paragraph.") for chunk in chunks)
    assert " ".join(chunk.text for chunk in chunks) == (sentence * 60).strip()


def test_text_of_the_columns_that_are_not_policy_is_never_returned(tmp_path: Path) -> None:
    # Guards the decision that the revision history, the cross references, the "other" text
    # and the keywords are not stored: they hold the code numbers and descriptor-like wording.
    batch = read_evidence_corpus(ncd_zip(tmp_path))

    returned = batch.model_dump_json()
    assert all(marker in ncd_csv().decode("utf-8") for marker in NEVER_STORED_MARKERS)
    assert not [marker for marker in NEVER_STORED_MARKERS if marker in returned]


@pytest.mark.parametrize("header", NEVER_STORED_HEADERS)
def test_a_column_that_is_not_policy_cannot_change_what_is_returned(
    tmp_path: Path, header: str
) -> None:
    plain = read_evidence_corpus(ncd_zip(tmp_path))
    edits = {(row, header): "<p>CHANGED-MARKER 99213 office visit</p>" for row in range(2, 7)}

    assert read_evidence_corpus(ncd_zip(tmp_path, edits)) == plain


def test_a_retired_notice_is_left_out_whatever_its_marker_looks_like(tmp_path: Path) -> None:
    edits = {(2, "NCD_mnl_sect_title"): "Example Blanket Therapy (RETIRED)"}

    batch = read_evidence_corpus(ncd_zip(tmp_path, edits))

    assert [entry.document.source_document_id for entry in batch.entries] == ["2", "5", "7"]
    assert batch.retired_skipped == 2


def test_the_same_file_always_gives_the_same_batch(tmp_path: Path) -> None:
    path = ncd_zip(tmp_path)

    assert read_evidence_corpus(path) == read_evidence_corpus(path)


def test_logs_the_counts(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO, logger="src.ingest.evidence_corpus"):
        read_evidence_corpus(ncd_zip(tmp_path))

    assert "read 4 determinations with 6 chunks, left out 1 retired notices" in caplog.text


# Gates: each one rejects the whole file.


@pytest.mark.parametrize(
    ("header", "value", "problem"),
    [
        ("NCD_id", "", "row 3: source_document_id: "),
        ("NCD_id", "0", "row 3: source_document_id: "),
        ("NCD_id", "12a", "row 3: source_document_id: "),
        ("NCD_mnl_sect", "", "row 3: section_number: "),
        ("NCD_mnl_sect", "10", "row 3: section_number: "),
        ("NCD_mnl_sect", "10.2 ", "row 3: section_number: "),
        ("NCD_mnl_sect_title", "", "row 3: title: "),
        ("NCD_mnl_sect_title", "   ", "row 3: title: "),
        ("NCD_mnl_sect_title", "x" * 301, "row 3: title: "),
        ("NCD_vrsn_num", "", "row 3: version_number: "),
        ("NCD_vrsn_num", "0", "row 3: version_number: "),
        ("NCD_vrsn_num", "1.0", "row 3: version_number: "),
        ("NCD_vrsn_num", " 1", "row 3: version_number: "),
        ("NCD_efctv_dt", "", "row 3: effective_date: "),
        ("NCD_efctv_dt", "1966-01-01", "row 3: effective_date: "),
        ("NCD_efctv_dt", "1966-13-01 00:00:00", "row 3: effective_date: "),
        ("NCD_efctv_dt", "01/01/1966 00:00:00", "row 3: effective_date: "),
    ],
)
def test_a_missing_or_malformed_required_field_rejects_the_file(
    tmp_path: Path, header: str, value: str, problem: str
) -> None:
    problems = _rejected(ncd_zip(tmp_path, {(3, header): value}))

    assert len(problems) == 1
    assert problems[0].startswith(problem)


def test_a_problem_does_not_show_the_cell_value(tmp_path: Path) -> None:
    problems = _rejected(ncd_zip(tmp_path, {(3, "NCD_efctv_dt"): "SECRET-VALUE"}))

    assert "SECRET-VALUE" not in "\n".join(problems)


def test_a_repeated_document_id_rejects_the_file(tmp_path: Path) -> None:
    problems = _rejected(ncd_zip(tmp_path, {(5, "NCD_id"): "1"}))

    assert problems == ["row 5: NCD_id appears more than once in the file"]


def test_a_document_id_repeated_by_a_retired_notice_rejects_the_file(tmp_path: Path) -> None:
    problems = _rejected(ncd_zip(tmp_path, {(4, "NCD_id"): "1"}))

    assert problems == ["row 4: NCD_id appears more than once in the file"]


def test_a_repeated_section_number_rejects_the_file(tmp_path: Path) -> None:
    problems = _rejected(ncd_zip(tmp_path, {(6, "NCD_mnl_sect"): "10.1"}))

    assert problems == ["row 6: NCD_mnl_sect appears more than once in the file"]


@pytest.mark.parametrize("blank", ["", "\r\n", "&#160;", "<p> </p>", "<ul><li></li></ul>"])
def test_a_document_with_no_policy_text_rejects_the_file(tmp_path: Path, blank: str) -> None:
    edits = {(5, "itm_srvc_desc"): blank, (5, "indctn_lmtn"): blank}

    assert _rejected(ncd_zip(tmp_path, edits)) == ["row 5: no policy text"]


def test_text_only_in_a_column_that_is_not_policy_does_not_count(tmp_path: Path) -> None:
    edits = {(5, "itm_srvc_desc"): "", (5, "rev_hstry"): "<p>Some made-up history.</p>"}

    assert _rejected(ncd_zip(tmp_path, edits)) == ["row 5: no policy text"]


def test_a_row_flagged_for_the_ama_notice_rejects_the_file(tmp_path: Path) -> None:
    problems = _rejected(ncd_zip(tmp_path, {(3, "NCD_AMA"): "True"}))

    assert problems == ["row 3: NCD_AMA is True: the text may hold third-party content"]


@pytest.mark.parametrize("value", ["", "true", "FALSE", "0", "No"])
def test_an_ama_flag_that_is_not_true_or_false_rejects_the_file(tmp_path: Path, value: str) -> None:
    problems = _rejected(ncd_zip(tmp_path, {(3, "NCD_AMA"): value}))

    assert problems == ["row 3: NCD_AMA must be True or False"]


def test_a_retired_notice_is_not_checked(tmp_path: Path) -> None:
    edits = {(4, "NCD_AMA"): "True", (4, "indctn_lmtn"): "", (4, "NCD_efctv_dt"): ""}

    assert read_evidence_corpus(ncd_zip(tmp_path, edits)).retired_skipped == 1


def test_an_unreadable_character_in_the_policy_text_rejects_the_file(tmp_path: Path) -> None:
    problems = _rejected(ncd_zip(tmp_path, {(3, "indctn_lmtn"): "<p>dam�ged</p>"}))

    assert problems == ["row 3: the policy text holds an unreadable character"]


def test_every_problem_of_a_file_is_reported_together(tmp_path: Path) -> None:
    edits = {(2, "NCD_AMA"): "True", (3, "NCD_vrsn_num"): "0", (6, "indctn_lmtn"): ""}
    edits[(6, "itm_srvc_desc")] = ""

    problems = _rejected(ncd_zip(tmp_path, edits))

    assert [problem.split(":")[0] for problem in problems] == ["row 2", "row 3", "row 6"]


def test_a_row_with_the_wrong_number_of_fields_rejects_the_file(tmp_path: Path) -> None:
    content = ncd_csv() + b"9,1,True\r\n"

    assert _rejected(ncd_zip(tmp_path, content=content)) == ["row 7: has 3 fields, expected 26"]


def test_a_file_with_no_data_rows_is_rejected(tmp_path: Path) -> None:
    content = (",".join(EXPECTED_HEADER) + "\r\n").encode("utf-8")

    assert _rejected(ncd_zip(tmp_path, content=content)) == ["no data rows"]


def test_a_file_with_only_retired_notices_is_rejected(tmp_path: Path) -> None:
    edits = {(row, "NCD_mnl_sect_title"): f"Example {row} - RETIRED" for row in range(2, 7)}

    assert _rejected(ncd_zip(tmp_path, edits)) == [
        "nothing to load: all 5 rows are retired notices"
    ]


def test_a_renamed_header_column_rejects_the_file(tmp_path: Path) -> None:
    content = ncd_csv().replace(b"NCD_AMA", b"NCD_ama", 1)

    assert _rejected(ncd_zip(tmp_path, content=content)) == ["row 1: column 26 should be 'NCD_AMA'"]


def test_a_header_with_a_missing_column_rejects_the_file(tmp_path: Path) -> None:
    content = ncd_csv().replace(b",NCD_AMA", b"", 1)

    assert _rejected(ncd_zip(tmp_path, content=content)) == [
        "row 1: header has 25 columns, expected 26"
    ]


def test_an_empty_csv_is_rejected(tmp_path: Path) -> None:
    assert _rejected(ncd_zip(tmp_path, content=b"")) == ["row 1: header has 0 columns, expected 26"]


def test_a_csv_that_is_not_utf8_is_rejected(tmp_path: Path) -> None:
    content = ncd_csv().replace(b"Example", b"Ex\xe4mple", 1)

    assert _rejected(ncd_zip(tmp_path, content=content)) == [f"{CSV_NAME} is not valid UTF-8 text"]


def test_a_csv_with_an_unclosed_quote_is_rejected(tmp_path: Path) -> None:
    content = ncd_csv() + b'8,1,True,2,40.1,"never closed\r\n'

    assert _rejected(ncd_zip(tmp_path, content=content)) == [f"{CSV_NAME} cannot be parsed"]


def test_a_zip_without_the_nested_zip_is_rejected(tmp_path: Path) -> None:
    path = ncd_zip(tmp_path, nested_name="other.zip")

    assert _rejected(path) == [f"the zip must hold {NESTED_ARCHIVE_NAME}"]


def test_a_nested_zip_without_the_csv_is_rejected(tmp_path: Path) -> None:
    path = ncd_zip(tmp_path, member="other.csv")

    assert _rejected(path) == [f"{NESTED_ARCHIVE_NAME} must hold {CSV_NAME}"]


def test_a_file_that_is_not_a_zip_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "ncd.zip"
    path.write_bytes(b"this is not a zip")

    assert _rejected(path) == ["not a readable .zip file"]


def test_a_nested_member_that_is_not_a_zip_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "ncd.zip"
    with ZipFile(path, "w", ZIP_DEFLATED) as archive:
        archive.writestr(NESTED_ARCHIVE_NAME, b"this is not a zip")

    assert _rejected(path) == ["not a readable .zip file"]


def test_a_damaged_zip_is_rejected(tmp_path: Path) -> None:
    whole = ncd_zip(tmp_path).read_bytes()
    path = tmp_path / "cut.zip"
    path.write_bytes(whole[: len(whole) // 2])

    assert _rejected(path) == ["not a readable .zip file"]


def test_a_zip_with_no_valid_date_for_the_csv_is_rejected(tmp_path: Path) -> None:
    path = ncd_zip(tmp_path, file_date=(1980, 0, 0))

    assert _rejected(path) == [f"the zip records no valid date for {CSV_NAME}"]


# Writing.


def test_replace_stores_every_document_and_chunk(session: Session, tmp_path: Path) -> None:
    batch = read_evidence_corpus(ncd_zip(tmp_path))

    written = replace_evidence_corpus(session, batch)

    assert written == (4, 6)
    assert _stored_counts(session) == (4, 6)


def test_replace_stores_every_field(session: Session, tmp_path: Path) -> None:
    replace_evidence_corpus(session, read_evidence_corpus(ncd_zip(tmp_path)))

    document = session.scalars(
        select(EvidenceDocument).where(
            EvidenceDocument.source == EvidenceSource.CMS_NCD,
            EvidenceDocument.source_document_id == "7",
        )
    ).one()
    chunks = session.scalars(
        select(EvidenceChunk)
        .where(EvidenceChunk.evidence_document_id == document.id)
        .order_by(EvidenceChunk.chunk_index)
    ).all()
    assert (
        document.section_number,
        document.title,
        document.version_number,
        document.effective_date,
        document.source_file_date,
    ) == (
        "220.6.17",
        "Example Imaging for Made-Up Conditions",
        11,
        date(2026, 6, 8),
        date(2026, 10, 5),
    )
    assert [
        (chunk.chunk_index, chunk.section_title, chunk.text, chunk.chunker_version)
        for chunk in chunks
    ] == [
        (0, DESCRIPTION_TITLE, "A. General\n\nMade-up imaging ≥ 2 times a year.", "v1"),
        (
            1,
            INDICATIONS_TITLE,
            "Loose text outside any tag.\n\nA second line after a break.",
            "v1",
        ),
    ]
    assert all(
        chunk.text_sha256 == hashlib.sha256(chunk.text.encode("utf-8")).hexdigest()
        for chunk in chunks
    )


def test_replacing_twice_leaves_the_same_rows_and_hashes(session: Session, tmp_path: Path) -> None:
    batch = read_evidence_corpus(ncd_zip(tmp_path))
    replace_evidence_corpus(session, batch)
    first = _stored_hashes(session)

    replace_evidence_corpus(session, batch)

    assert _stored_hashes(session) == first
    assert len(first) == 6


def test_a_document_that_left_the_file_is_removed_with_its_chunks(
    session: Session, tmp_path: Path
) -> None:
    replace_evidence_corpus(session, read_evidence_corpus(ncd_zip(tmp_path)))
    retired_now = {(2, "NCD_mnl_sect_title"): "Example Blanket Therapy - RETIRED"}

    written = replace_evidence_corpus(session, read_evidence_corpus(ncd_zip(tmp_path, retired_now)))

    assert written == (3, 4)
    assert _stored_counts(session) == (3, 4)
    assert "1" not in {document_id for document_id, _, _ in _stored_hashes(session)}


def test_replacing_with_an_empty_batch_removes_every_row(session: Session, tmp_path: Path) -> None:
    replace_evidence_corpus(session, read_evidence_corpus(ncd_zip(tmp_path)))

    written = replace_evidence_corpus(session, EvidenceCorpusBatch(entries=[], retired_skipped=0))

    assert written == (0, 0)
    assert _stored_counts(session) == (0, 0)


def test_the_fixture_zip_is_shaped_like_the_download(tmp_path: Path) -> None:
    with ZipFile(ncd_zip(tmp_path)) as outer:
        nested = ZipFile(io.BytesIO(outer.read(NESTED_ARCHIVE_NAME)))

    assert nested.namelist() == [CSV_NAME]
