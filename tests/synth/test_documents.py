from dataclasses import dataclass
from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from src.db.models import (
    ClaimSample,
    ClaimSampleLabel,
    ClaimSampleLine,
    DatasetSplit,
    DenialReasonCategory,
    DocumentType,
    GeneratedDocument,
)
from src.ingest.marketplace_denials import BatchRejectedError
from src.ml.labels import LABEL_RULE_VERSION, ClaimLabel
from src.synth.denial_letter import (
    GENERATOR_VERSION,
    TEMPLATE_IDS,
    DenialLetterAnswerKey,
    DeniedLineKey,
    generate_denial_letter,
)
from src.synth.documents import (
    GeneratedDocumentRow,
    build_denial_letter_rows,
    replace_generated_document_rows,
)
from src.synth.identity import MAX_SEED

SEED = 42
PAID_CLAIM = "800000000000001"
DENIED_CLAIM = "800000000000007"
OTHER_DENIED_CLAIM = "800000000000003"
FROM_DATE = date(2009, 3, 1)
THRU_DATE = date(2009, 3, 2)
DIAGNOSIS_CODES = ("4019", "25000")


@dataclass(frozen=True)
class Claim:
    """A made-up claim with the four fields the letter prints."""

    source_claim_id: str
    claim_from_date: date
    claim_thru_date: date
    diagnosis_codes: tuple[str, ...]


@dataclass(frozen=True)
class Line:
    """A made-up service line with the fields the letter reads."""

    line_number: int
    hcpcs_code: str | None
    processing_indicator: str
    payment_amount: Decimal
    allowed_charge_amount: Decimal


# One denied line (N, nothing paid) and one paid line, which only counts in the totals.
DENIED_CLAIM_LINES = (
    Line(2, "99213", "N", Decimal("0.00"), Decimal("0.00")),
    Line(1, "71020", "A", Decimal("80.00"), Decimal("100.00")),
)
PAID_CLAIM_LINES = (Line(1, "99214", "A", Decimal("40.00"), Decimal("50.00")),)
OTHER_DENIED_CLAIM_LINES = (Line(1, None, "N", Decimal("0.00"), Decimal("25.00")),)


def _store_claim(
    session: Session,
    claim_id: str,
    lines: tuple[Line, ...],
    is_denied: bool,
    rule_version: str = LABEL_RULE_VERSION,
) -> None:
    """Store one made-up claim with its lines and its label."""
    session.add(
        ClaimSample(
            source_claim_id=claim_id,
            claim_from_date=FROM_DATE,
            claim_thru_date=THRU_DATE,
            diagnosis_codes=list(DIAGNOSIS_CODES),
        )
    )
    session.flush()  # the lines and the label point at the claim
    for line in lines:
        session.add(
            ClaimSampleLine(
                source_claim_id=claim_id,
                line_number=line.line_number,
                hcpcs_code=line.hcpcs_code,
                processing_indicator=line.processing_indicator,
                payment_amount=line.payment_amount,
                deductible_amount=Decimal("0.00"),
                primary_payer_paid_amount=Decimal("0.00"),
                coinsurance_amount=Decimal("0.00"),
                allowed_charge_amount=line.allowed_charge_amount,
            )
        )
    session.add(
        ClaimSampleLabel(
            source_claim_id=claim_id,
            is_denied=is_denied,
            denial_reason_category=DenialReasonCategory.NONCOVERED if is_denied else None,
            appeal_success_proxy=False if is_denied else None,
            label_rule_version=rule_version,
            split=DatasetSplit.TRAIN,
            split_seed=SEED,
        )
    )
    session.flush()


def _store_all(session: Session) -> None:
    """Two denied claims and one paid claim."""
    _store_claim(session, DENIED_CLAIM, DENIED_CLAIM_LINES, is_denied=True)
    _store_claim(session, PAID_CLAIM, PAID_CLAIM_LINES, is_denied=False)
    _store_claim(session, OTHER_DENIED_CLAIM, OTHER_DENIED_CLAIM_LINES, is_denied=True)


def _stored(session: Session) -> dict[str, GeneratedDocument]:
    session.expire_all()
    return {
        document.source_claim_id: document
        for document in session.scalars(select(GeneratedDocument))
    }


def _row(claim_id: str = DENIED_CLAIM, seed: int = SEED) -> GeneratedDocumentRow:
    """A valid row that needs no stored lines or label."""
    return GeneratedDocumentRow(
        source_claim_id=claim_id,
        document_type=DocumentType.DENIAL_LETTER,
        template_id="formal_letter",
        seed=seed,
        generator_version=GENERATOR_VERSION,
        text="A made-up letter.",
        answer_key={"claim_number": claim_id},
    )


def test_builds_one_letter_per_denied_claim_and_none_for_a_paid_claim(session: Session) -> None:
    _store_all(session)

    rows = build_denial_letter_rows(session, SEED)

    # In claim id order, whatever order the claims were stored in.
    assert [row.source_claim_id for row in rows] == [OTHER_DENIED_CLAIM, DENIED_CLAIM]
    assert {row.document_type for row in rows} == {DocumentType.DENIAL_LETTER}
    assert {row.seed for row in rows} == {SEED}
    assert {row.generator_version for row in rows} == {GENERATOR_VERSION}
    assert all(row.template_id in TEMPLATE_IDS for row in rows)


def test_a_row_is_the_generators_letter_for_the_stored_claim_lines_and_label(
    session: Session,
) -> None:
    _store_all(session)
    expected = generate_denial_letter(
        Claim(DENIED_CLAIM, FROM_DATE, THRU_DATE, DIAGNOSIS_CODES),
        DENIED_CLAIM_LINES,
        ClaimLabel(
            source_claim_id=DENIED_CLAIM,
            is_denied=True,
            denial_reason_category=DenialReasonCategory.NONCOVERED,
            appeal_success_proxy=False,
            label_rule_version=LABEL_RULE_VERSION,
        ),
        SEED,
    )

    row = build_denial_letter_rows(session, SEED)[1]

    assert row.text == expected.text
    assert row.template_id == expected.template_id
    assert row.answer_key == expected.answer_key.model_dump(mode="json")
    # Only this claim's lines were used: the totals are its two lines, not the other claims'.
    assert row.answer_key["total_allowed_charge_amount"] == "100.00"
    assert [line["line_number"] for line in row.answer_key["denied_lines"]] == [2]


def test_same_seed_gives_identical_rows_and_another_seed_gives_other_text(
    session: Session,
) -> None:
    _store_all(session)

    first = build_denial_letter_rows(session, SEED)

    assert build_denial_letter_rows(session, SEED) == first
    other = build_denial_letter_rows(session, SEED + 1)
    assert [row.text for row in other] != [row.text for row in first]
    assert {row.seed for row in other} == {SEED + 1}


def test_building_writes_nothing(session: Session) -> None:
    _store_all(session)

    build_denial_letter_rows(session, SEED)

    assert _stored(session) == {}


def test_rejects_when_no_claim_is_labelled_denied(session: Session) -> None:
    _store_claim(session, PAID_CLAIM, PAID_CLAIM_LINES, is_denied=False)

    with pytest.raises(BatchRejectedError, match="no claim is labelled denied"):
        build_denial_letter_rows(session, SEED)


def test_rejects_labels_made_by_another_rule_version(session: Session) -> None:
    _store_claim(session, DENIED_CLAIM, DENIED_CLAIM_LINES, is_denied=True)
    _store_claim(
        session, OTHER_DENIED_CLAIM, OTHER_DENIED_CLAIM_LINES, is_denied=True, rule_version="v0"
    )

    with pytest.raises(BatchRejectedError, match="rule version v0, the code is at v1"):
        build_denial_letter_rows(session, SEED)


@pytest.mark.parametrize("lines", [PAID_CLAIM_LINES, ()], ids=["only a paid line", "no lines"])
def test_rejects_a_denied_claim_whose_stored_lines_hold_no_denied_line(
    session: Session, lines: tuple[Line, ...]
) -> None:
    _store_claim(session, DENIED_CLAIM, DENIED_CLAIM_LINES, is_denied=True)
    # Labelled denied, but no stored line is denied: the lines changed after the label.
    _store_claim(session, OTHER_DENIED_CLAIM, lines, is_denied=True)

    with pytest.raises(BatchRejectedError) as excinfo:
        build_denial_letter_rows(session, SEED)

    assert excinfo.value.problems == [
        f"claim {OTHER_DENIED_CLAIM} is labelled denied but has no denied line: run"
        " pipelines.run_data_pipeline again"
    ]


@pytest.mark.parametrize("seed", [-1, MAX_SEED + 1])
def test_rejects_a_seed_that_does_not_fit_the_integer_column(session: Session, seed: int) -> None:
    _store_all(session)

    with pytest.raises(ValueError, match="seed must be from 0 to"):
        build_denial_letter_rows(session, seed)


@pytest.mark.parametrize("seed", [-1, MAX_SEED + 1])
def test_row_model_rejects_a_seed_outside_the_integer_column(seed: int) -> None:
    with pytest.raises(ValidationError, match="seed"):
        _row(seed=seed)


def test_replace_stores_the_rows_and_the_answer_key_reads_back(session: Session) -> None:
    _store_all(session)
    rows = build_denial_letter_rows(session, SEED)

    written = replace_generated_document_rows(session, rows)

    stored = _stored(session)
    assert written == 2
    assert set(stored) == {DENIED_CLAIM, OTHER_DENIED_CLAIM}
    document = stored[DENIED_CLAIM]
    assert (document.document_type, document.seed, document.generator_version) == (
        DocumentType.DENIAL_LETTER,
        SEED,
        GENERATOR_VERSION,
    )
    assert document.text == rows[1].text
    # The stored JSON turns back into the typed answer key, with real dates and amounts.
    key = DenialLetterAnswerKey.model_validate(document.answer_key)
    assert key.claim_number == DENIED_CLAIM
    assert (key.service_from_date, key.service_thru_date) == (FROM_DATE, THRU_DATE)
    assert key.total_payment_amount == Decimal("80.00")
    assert key.model_dump(mode="json") == rows[1].answer_key


def test_stored_answer_key_has_exactly_the_fields_of_the_answer_key_model(
    session: Session,
) -> None:
    _store_all(session)

    replace_generated_document_rows(session, build_denial_letter_rows(session, SEED))

    for document in _stored(session).values():
        assert set(document.answer_key) == set(DenialLetterAnswerKey.model_fields)
        for line in document.answer_key["denied_lines"]:
            assert set(line) == set(DeniedLineKey.model_fields)


def test_replace_leaves_only_the_given_rows(session: Session) -> None:
    _store_all(session)
    replace_generated_document_rows(session, build_denial_letter_rows(session, SEED))

    # A later run in which only one claim is still denied, with another seed.
    written = replace_generated_document_rows(session, [_row(DENIED_CLAIM, seed=7)])

    stored = _stored(session)
    assert written == 1
    assert set(stored) == {DENIED_CLAIM}
    assert stored[DENIED_CLAIM].seed == 7


def test_replace_with_no_rows_empties_the_table(session: Session) -> None:
    _store_all(session)
    replace_generated_document_rows(session, build_denial_letter_rows(session, SEED))

    assert replace_generated_document_rows(session, []) == 0

    assert _stored(session) == {}


def test_replace_keeps_the_claims_and_their_labels(session: Session) -> None:
    _store_all(session)

    replace_generated_document_rows(session, build_denial_letter_rows(session, SEED))

    assert session.scalar(select(func.count()).select_from(ClaimSample)) == 3
    assert session.scalar(select(func.count()).select_from(ClaimSampleLabel)) == 3


def test_replace_rejects_a_document_whose_claim_is_not_stored(session: Session) -> None:
    with pytest.raises(IntegrityError, match="fk_generated_documents_claim"):
        replace_generated_document_rows(session, [_row()])
