"""Claims reader tests. The fixture has six made-up claims in csv rows 2 to 7.

Row 2 is a clean one-line claim, row 3 has three lines, and rows 4 to 6 carry the oddities
the published file has (no procedure code, an undefined indicator, payment above allowed).
"""

import logging
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from src.db.models import CLAIM_LINE_AMOUNT_COLUMNS, ClaimSample, ClaimSampleLine
from src.ingest import claims_sample
from src.ingest.claims_sample import (
    AMOUNT_FAMILIES,
    EXPECTED_HEADER,
    ClaimSampleBatch,
    ClaimSampleLineRow,
    ClaimSampleRow,
    read_claim_sample_rows,
    upsert_claim_sample_batch,
)
from src.ingest.marketplace_denials import BatchRejectedError
from tests.ingest.helpers import CLAIMS_FIXTURE, claims_zip, zipped

ZERO = Decimal("0.00")


def _rejected(path: Path, max_claims: int | None = None) -> list[str]:
    with pytest.raises(BatchRejectedError) as excinfo:
        read_claim_sample_rows(path, max_claims)
    return excinfo.value.problems


def test_returns_one_claim_per_row_and_one_line_per_used_slot(tmp_path: Path) -> None:
    batch = read_claim_sample_rows(claims_zip(tmp_path))

    assert [claim.source_claim_id for claim in batch.claims] == [
        f"80000000000000{number}" for number in range(1, 7)
    ]
    assert [(line.source_claim_id[-1], line.line_number) for line in batch.lines] == [
        ("1", 1),
        ("2", 1),
        ("2", 2),
        ("2", 3),
        ("3", 1),
        ("4", 1),
        ("5", 1),
        ("5", 2),
        ("6", 1),
    ]


def test_reads_every_field_of_a_claim_and_its_line(tmp_path: Path) -> None:
    batch = read_claim_sample_rows(claims_zip(tmp_path))

    assert batch.claims[0] == ClaimSampleRow(
        source_claim_id="800000000000001",
        claim_from_date=date(2009, 3, 1),
        claim_thru_date=date(2009, 3, 1),
        diagnosis_codes=["4019", "V5869"],
    )
    assert batch.lines[0] == ClaimSampleLineRow(
        source_claim_id="800000000000001",
        line_number=1,
        hcpcs_code="99213",
        line_diagnosis_code="4019",
        processing_indicator="A",
        payment_amount=Decimal("50.00"),
        deductible_amount=ZERO,
        primary_payer_paid_amount=ZERO,
        coinsurance_amount=Decimal("10.00"),
        allowed_charge_amount=Decimal("60.00"),
    )


def test_keeps_an_unpaid_line_with_zero_amounts(tmp_path: Path) -> None:
    unpaid = read_claim_sample_rows(claims_zip(tmp_path)).lines[2]

    assert unpaid.processing_indicator == "C"
    assert [getattr(unpaid, name) for name in CLAIM_LINE_AMOUNT_COLUMNS] == [ZERO] * 5


def test_drops_empty_diagnosis_slots_and_keeps_the_order(tmp_path: Path) -> None:
    claims = read_claim_sample_rows(claims_zip(tmp_path)).claims

    assert claims[2].diagnosis_codes == ["V5869", "42731"]  # slots 2 and 4 in the file
    assert claims[5].diagnosis_codes == []


def test_keeps_codes_as_text_and_empty_codes_as_none(tmp_path: Path) -> None:
    lines = read_claim_sample_rows(claims_zip(tmp_path)).lines

    assert lines[4].hcpcs_code is None
    assert lines[7].hcpcs_code == "01996"  # the leading zero survives
    assert lines[8].line_diagnosis_code is None
    assert lines[7].primary_payer_paid_amount == Decimal("1200.00")


def test_max_claims_stops_after_that_many_rows(tmp_path: Path) -> None:
    batch = read_claim_sample_rows(claims_zip(tmp_path), max_claims=2)

    assert len(batch.claims) == 2
    assert len(batch.lines) == 4


def test_rows_past_max_claims_are_not_checked(tmp_path: Path) -> None:
    path = claims_zip(tmp_path, {(4, "CLM_ID"): "not-an-id"})

    assert len(read_claim_sample_rows(path, max_claims=2).claims) == 2
    assert _rejected(path, max_claims=3) != []


def test_reads_a_csv_with_lf_line_endings(tmp_path: Path) -> None:
    path = zipped(tmp_path, CLAIMS_FIXTURE.read_bytes())

    assert len(read_claim_sample_rows(path).claims) == 6


def test_amount_fields_match_the_table_columns() -> None:
    assert tuple(AMOUNT_FAMILIES) == CLAIM_LINE_AMOUNT_COLUMNS


def test_rejects_a_file_that_is_not_a_zip(tmp_path: Path) -> None:
    path = tmp_path / "claims.zip"
    path.write_bytes(CLAIMS_FIXTURE.read_bytes())

    assert _rejected(path) == ["not a readable .zip file"]


def test_rejects_a_zip_cut_short(tmp_path: Path) -> None:
    whole = claims_zip(tmp_path).read_bytes()
    path = tmp_path / "cut.zip"
    path.write_bytes(whole[: len(whole) // 2])

    assert _rejected(path) == ["not a readable .zip file"]


def test_rejects_a_zip_with_damaged_content(tmp_path: Path) -> None:
    path = claims_zip(tmp_path)
    damaged = bytearray(path.read_bytes())
    for index in range(200, 260):
        damaged[index] ^= 0xFF
    path.write_bytes(bytes(damaged))

    assert _rejected(path) == ["not a readable .zip file"]


def test_rejects_a_zip_that_does_not_hold_one_csv(tmp_path: Path) -> None:
    path = zipped(tmp_path, CLAIMS_FIXTURE.read_bytes(), member="claims.txt")

    assert _rejected(path) == ["the zip must hold exactly one .csv file"]


def test_rejects_text_that_is_not_utf8(tmp_path: Path) -> None:
    path = zipped(tmp_path, CLAIMS_FIXTURE.read_bytes() + b"\xff\xfe\r\n")

    assert _rejected(path) == ["the csv is not valid UTF-8 text"]


def test_rejects_a_csv_that_cannot_be_parsed(tmp_path: Path) -> None:
    oversized_field = b"x" * 200_000  # above the csv module's field size limit

    assert _rejected(zipped(tmp_path, CLAIMS_FIXTURE.read_bytes() + oversized_field)) == [
        "the csv cannot be parsed"
    ]


def test_rejects_a_renamed_header(tmp_path: Path) -> None:
    path = claims_zip(tmp_path, {(1, "CLM_ID"): "CLAIM_ID"})

    assert _rejected(path) == ["row 1: column 2 should be 'CLM_ID'"]


def test_rejects_a_header_with_a_missing_column(tmp_path: Path) -> None:
    lines = CLAIMS_FIXTURE.read_text(encoding="utf-8").splitlines()
    lines[0] = lines[0].rsplit(",", 1)[0]
    path = zipped(tmp_path, "\n".join(lines).encode())

    assert _rejected(path) == [f"row 1: header has 141 columns, expected {len(EXPECTED_HEADER)}"]


def test_rejects_an_empty_csv(tmp_path: Path) -> None:
    assert _rejected(zipped(tmp_path, b"")) == ["row 1: header has 0 columns, expected 142"]


def test_rejects_a_file_with_no_data_rows(tmp_path: Path) -> None:
    header = CLAIMS_FIXTURE.read_text(encoding="utf-8").splitlines()[0]

    assert _rejected(zipped(tmp_path, header.encode())) == ["no data rows"]


def test_rejects_a_row_with_the_wrong_number_of_fields(tmp_path: Path) -> None:
    content = CLAIMS_FIXTURE.read_bytes() + b"800000000000007,20090101\n"

    assert _rejected(zipped(tmp_path, content)) == ["row 8: has 2 fields, expected 142"]


@pytest.mark.parametrize("claim_id", ["", "80000000000001", "80000000000000A"])
def test_rejects_a_claim_id_that_is_not_fifteen_digits(tmp_path: Path, claim_id: str) -> None:
    problems = _rejected(claims_zip(tmp_path, {(3, "CLM_ID"): claim_id}))

    assert len(problems) == 1
    assert problems[0].startswith("row 3: source_claim_id: ")


def test_rejects_a_claim_id_that_appears_twice(tmp_path: Path) -> None:
    path = claims_zip(tmp_path, {(5, "CLM_ID"): "800000000000001"})

    assert _rejected(path) == ["row 5: claim id appears more than once in the file"]


@pytest.mark.parametrize("text", ["", "2009-03-01", "20090231", "2009030", "200903011"])
def test_rejects_a_date_that_is_not_yyyymmdd(tmp_path: Path, text: str) -> None:
    problems = _rejected(claims_zip(tmp_path, {(2, "CLM_FROM_DT"): text}))

    assert problems == ["row 2: claim_from_date: Value error, must be a date written as YYYYMMDD"]


@pytest.mark.parametrize(
    ("header", "text"), [("CLM_FROM_DT", "20071231"), ("CLM_THRU_DT", "20110101")]
)
def test_rejects_a_date_outside_2008_to_2010(tmp_path: Path, header: str, text: str) -> None:
    problems = _rejected(claims_zip(tmp_path, {(2, header): text}))

    assert len(problems) == 1
    assert problems[0].startswith("row 2: claim_")


def test_rejects_a_claim_that_ends_before_it_starts(tmp_path: Path) -> None:
    path = claims_zip(tmp_path, {(2, "CLM_FROM_DT"): "20090302"})

    assert _rejected(path) == ["row 2: row: Value error, claim from date is after claim thru date"]


@pytest.mark.parametrize("family", AMOUNT_FAMILIES.values())
@pytest.mark.parametrize("text", ["", "abc", "-10.00", "10.005"])
def test_rejects_a_bad_amount_on_a_used_line(tmp_path: Path, family: str, text: str) -> None:
    problems = _rejected(claims_zip(tmp_path, {(3, f"{family}_2"): text}))

    assert len(problems) == 1
    assert problems[0].startswith("row 3: line 2: ")
    assert "_amount: " in problems[0]


def test_rejects_a_claim_with_no_used_line(tmp_path: Path) -> None:
    path = claims_zip(
        tmp_path,
        {
            (7, "TAX_NUM_1"): "",
            (7, "LINE_PRCSG_IND_CD_1"): "",
            (7, "PRF_PHYSN_NPI_1"): "",
            (7, "HCPCS_CD_1"): "",
        },
    )

    assert _rejected(path) == ["row 7: no used service line"]


def test_rejects_used_lines_with_a_gap(tmp_path: Path) -> None:
    edits = {
        (2, "TAX_NUM_3"): "100000001",
        (2, "LINE_PRCSG_IND_CD_3"): "A",
    }

    assert _rejected(claims_zip(tmp_path, edits)) == [
        "row 2: used service lines are not in a row starting at line 1"
    ]


def test_rejects_a_line_with_only_one_of_tax_number_and_indicator(tmp_path: Path) -> None:
    path = claims_zip(tmp_path, {(3, "TAX_NUM_2"): "", (3, "LINE_PRCSG_IND_CD_3"): ""})

    assert _rejected(path) == [
        "row 3: line 2: indicator without a tax number",
        "row 3: line 3: tax number without an indicator",
    ]


@pytest.mark.parametrize(
    ("header", "text"),
    [
        ("HCPCS_CD_2", "99213"),
        ("PRF_PHYSN_NPI_2", "1000000001"),
        ("LINE_ICD9_DGNS_CD_13", "4019"),
        ("LINE_NCH_PMT_AMT_2", "10.00"),
        ("LINE_ALOWD_CHRG_AMT_13", ""),
    ],
)
def test_rejects_values_in_an_unused_line(tmp_path: Path, header: str, text: str) -> None:
    slot = header.rsplit("_", 1)[1]

    assert _rejected(claims_zip(tmp_path, {(2, header): text})) == [
        f"row 2: line {slot}: values in a line with no tax number and no indicator"
    ]


@pytest.mark.parametrize(
    ("header", "text", "field"),
    [
        ("LINE_PRCSG_IND_CD_1", "AB", "processing_indicator"),
        ("HCPCS_CD_1", "9921", "hcpcs_code"),
        ("HCPCS_CD_1", "992133", "hcpcs_code"),
        ("LINE_ICD9_DGNS_CD_1", "401900", "line_diagnosis_code"),
    ],
)
def test_rejects_a_line_code_of_the_wrong_length(
    tmp_path: Path, header: str, text: str, field: str
) -> None:
    problems = _rejected(claims_zip(tmp_path, {(2, header): text}))

    assert len(problems) == 1
    assert problems[0].startswith(f"row 2: line 1: {field}: ")


def test_rejects_a_claim_diagnosis_code_that_is_too_long(tmp_path: Path) -> None:
    problems = _rejected(claims_zip(tmp_path, {(2, "ICD9_DGNS_CD_2"): "V58690"}))

    assert len(problems) == 1
    assert problems[0].startswith("row 2: diagnosis_codes.1: ")


def test_reports_every_bad_row_and_returns_nothing(tmp_path: Path) -> None:
    edits = {(2, "CLM_ID"): "1", (4, "CLM_THRU_DT"): "x", (6, "LINE_NCH_PMT_AMT_1"): "x"}

    problems = _rejected(claims_zip(tmp_path, edits))

    assert [problem.split(":")[0] for problem in problems] == ["row 2", "row 4", "row 6"]


def test_stops_collecting_after_the_problem_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(claims_sample, "MAX_PROBLEMS_COLLECTED", 2)
    edits = {(row, "CLM_ID"): "1" for row in range(2, 8)}

    assert len(_rejected(claims_zip(tmp_path, edits))) == 2


def test_problem_messages_do_not_contain_cell_values(tmp_path: Path) -> None:
    edits = {
        (2, "CLM_ID"): "SECRET-ID",
        (3, "LINE_NCH_PMT_AMT_1"): "SECRET-AMOUNT",
        (4, "HCPCS_CD_2"): "SECRT",
    }

    problems = _rejected(claims_zip(tmp_path, edits))

    assert len(problems) == 3
    assert "SECR" not in "\n".join(problems)


def test_a_clean_claim_logs_no_warnings(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING):
        read_claim_sample_rows(claims_zip(tmp_path), max_claims=2)

    assert caplog.records == []


def test_warns_once_about_each_oddity_the_published_file_has(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.WARNING):
        batch = read_claim_sample_rows(claims_zip(tmp_path))

    assert len(batch.claims) == 6  # warnings do not reject
    assert [record.getMessage() for record in caplog.records] == [
        "1 line(s) use a processing indicator that is not in the CMS codebook: H",
        "1 line(s) have a payment above the allowed charge",
        "1 line(s) have no procedure code",
    ]


def test_logs_how_many_claims_and_lines_were_read(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO):
        read_claim_sample_rows(claims_zip(tmp_path))

    assert "read 6 claims with 9 service lines" in caplog.text


def _stored_counts(session: Session) -> tuple[int, int]:
    """(claim rows, line rows)."""
    return (
        session.scalar(select(func.count()).select_from(ClaimSample)) or 0,
        session.scalar(select(func.count()).select_from(ClaimSampleLine)) or 0,
    )


def test_upsert_writes_every_claim_and_line(session: Session, tmp_path: Path) -> None:
    batch = read_claim_sample_rows(claims_zip(tmp_path))

    written = upsert_claim_sample_batch(session, batch)

    assert written == (6, 9)
    assert _stored_counts(session) == (6, 9)
    stored = session.scalars(
        select(ClaimSample).where(ClaimSample.source_claim_id == "800000000000003")
    ).one()
    assert stored.diagnosis_codes == ["V5869", "42731"]
    paid_by_other = session.scalars(
        select(ClaimSampleLine).where(
            ClaimSampleLine.source_claim_id == "800000000000005", ClaimSampleLine.line_number == 2
        )
    ).one()
    assert paid_by_other.primary_payer_paid_amount == Decimal("1200.00")
    assert paid_by_other.processing_indicator == "<"


def test_second_upsert_keeps_the_same_rows(session: Session, tmp_path: Path) -> None:
    batch = read_claim_sample_rows(claims_zip(tmp_path))
    upsert_claim_sample_batch(session, batch)

    upsert_claim_sample_batch(session, batch)

    assert _stored_counts(session) == (6, 9)


def test_upsert_updates_changed_values_in_place_and_advances_updated_at(
    session: Session, tmp_path: Path
) -> None:
    upsert_claim_sample_batch(session, read_claim_sample_rows(claims_zip(tmp_path)))
    long_ago = datetime(2020, 1, 1, tzinfo=UTC)
    session.execute(update(ClaimSample).values(updated_at=long_ago))
    session.execute(update(ClaimSampleLine).values(updated_at=long_ago))
    corrected = claims_zip(
        tmp_path, {(2, "ICD9_DGNS_CD_1"): "2724", (2, "LINE_NCH_PMT_AMT_1"): "40.00"}
    )

    upsert_claim_sample_batch(session, read_claim_sample_rows(corrected))
    session.expire_all()

    claim = session.scalars(
        select(ClaimSample).where(ClaimSample.source_claim_id == "800000000000001")
    ).one()
    line = session.scalars(
        select(ClaimSampleLine).where(ClaimSampleLine.source_claim_id == "800000000000001")
    ).one()
    assert claim.diagnosis_codes == ["2724", "V5869"]
    assert line.payment_amount == Decimal("40.00")
    assert claim.updated_at > long_ago
    assert line.updated_at > long_ago
    assert _stored_counts(session) == (6, 9)


def test_upsert_refuses_lines_without_their_claim(session: Session, tmp_path: Path) -> None:
    batch = read_claim_sample_rows(claims_zip(tmp_path))
    orphans = ClaimSampleBatch(claims=[], lines=batch.lines)

    with pytest.raises(IntegrityError, match="fk_claim_sample_lines_claim"):
        upsert_claim_sample_batch(session, orphans)


def test_upsert_of_an_empty_batch_writes_nothing(session: Session) -> None:
    assert upsert_claim_sample_batch(session, ClaimSampleBatch(claims=[], lines=[])) == (0, 0)
    assert _stored_counts(session) == (0, 0)
