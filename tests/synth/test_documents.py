from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

import pytest
from pydantic import BaseModel, ValidationError
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
from src.synth import clinical_note, denial_letter, prior_auth
from src.synth.clinical_note import ClinicalNoteAnswerKey, generate_clinical_note
from src.synth.denial_letter import (
    GENERATOR_VERSION,
    DenialLetterAnswerKey,
    DeniedLineKey,
    generate_denial_letter,
)
from src.synth.documents import (
    GENERATOR_VERSIONS,
    NOISE_NOT_APPLIED_RECORD,
    NOISE_NOT_APPLIED_VERSION,
    GeneratedDocumentRow,
    build_document_rows,
    count_by_type,
    replace_generated_document_rows,
)
from src.synth.identity import MAX_SEED
from src.synth.prior_auth import PriorAuthAnswerKey, generate_prior_auth

LETTER = DocumentType.DENIAL_LETTER
NOTE = DocumentType.CLINICAL_NOTE
PRIOR_AUTH = DocumentType.PRIOR_AUTH

SEED = 42
PAID_CLAIM = "800000000000001"
DENIED_CLAIM = "800000000000007"
OTHER_DENIED_CLAIM = "800000000000003"
DUPLICATE_CLAIM = "800000000000005"
FROM_DATE = date(2009, 3, 1)
THRU_DATE = date(2009, 3, 2)
DIAGNOSIS_CODES = ("4019", "25000")
NONCOVERED = DenialReasonCategory.NONCOVERED

ANSWER_KEY_MODELS: dict[DocumentType, type[BaseModel]] = {
    LETTER: DenialLetterAnswerKey,
    NOTE: ClinicalNoteAnswerKey,
    PRIOR_AUTH: PriorAuthAnswerKey,
}
TEMPLATE_IDS: dict[DocumentType, tuple[str, ...]] = {
    LETTER: denial_letter.TEMPLATE_IDS,
    NOTE: clinical_note.TEMPLATE_IDS,
    PRIOR_AUTH: prior_auth.TEMPLATE_IDS,
}
GENERATORS: dict[DocumentType, Callable[..., Any]] = {
    LETTER: generate_denial_letter,
    NOTE: generate_clinical_note,
    PRIOR_AUTH: generate_prior_auth,
}


@dataclass(frozen=True)
class Claim:
    """A made-up claim with the four fields the documents print."""

    source_claim_id: str
    claim_from_date: date
    claim_thru_date: date
    diagnosis_codes: tuple[str, ...]


@dataclass(frozen=True)
class Line:
    """A made-up service line with the fields the generators read."""

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
# Denied as a duplicate (M): a letter and a note, but no prior-authorisation record.
DUPLICATE_CLAIM_LINES = (Line(1, "99215", "M", Decimal("0.00"), Decimal("30.00")),)


def _store_claim(
    session: Session,
    claim_id: str,
    lines: tuple[Line, ...],
    is_denied: bool,
    rule_version: str = LABEL_RULE_VERSION,
    category: DenialReasonCategory = NONCOVERED,
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
            denial_reason_category=category if is_denied else None,
            appeal_success_proxy=False if is_denied else None,
            label_rule_version=rule_version,
            split=DatasetSplit.TRAIN,
            split_seed=SEED,
        )
    )
    session.flush()


def _store_all(session: Session) -> None:
    """Three denied claims, two of which get a prior-authorisation record, and one paid claim."""
    _store_claim(session, DENIED_CLAIM, DENIED_CLAIM_LINES, is_denied=True)
    _store_claim(session, PAID_CLAIM, PAID_CLAIM_LINES, is_denied=False)
    _store_claim(session, OTHER_DENIED_CLAIM, OTHER_DENIED_CLAIM_LINES, is_denied=True)
    _store_claim(
        session,
        DUPLICATE_CLAIM,
        DUPLICATE_CLAIM_LINES,
        is_denied=True,
        category=DenialReasonCategory.DUPLICATE,
    )


def _stored(session: Session) -> dict[tuple[str, DocumentType], GeneratedDocument]:
    session.expire_all()
    return {
        (document.source_claim_id, document.document_type): document
        for document in session.scalars(select(GeneratedDocument))
    }


def _row(
    claim_id: str = DENIED_CLAIM, seed: int = SEED, document_type: DocumentType = LETTER
) -> GeneratedDocumentRow:
    """A valid row that needs no stored lines or label."""
    return GeneratedDocumentRow(
        source_claim_id=claim_id,
        document_type=document_type,
        template_id="formal_letter",
        seed=seed,
        generator_version=GENERATOR_VERSION,
        text="A made-up letter.",
        answer_key={"claim_number": claim_id},
        noise_version=NOISE_NOT_APPLIED_VERSION,
        noise_record=NOISE_NOT_APPLIED_RECORD,
    )


def _built(session: Session, claim_id: str, document_type: DocumentType) -> GeneratedDocumentRow:
    (row,) = (
        row
        for row in build_document_rows(session, SEED)
        if (row.source_claim_id, row.document_type) == (claim_id, document_type)
    )
    return row


def test_builds_a_letter_and_a_note_per_denied_claim_and_a_record_where_one_is_needed(
    session: Session,
) -> None:
    _store_all(session)

    rows = build_document_rows(session, SEED)

    # In claim id order, whatever order the claims were stored in; per claim letter, note,
    # record. The paid claim gets nothing, the duplicate claim no record.
    assert [(row.source_claim_id, row.document_type) for row in rows] == [
        (OTHER_DENIED_CLAIM, LETTER),
        (OTHER_DENIED_CLAIM, NOTE),
        (OTHER_DENIED_CLAIM, PRIOR_AUTH),
        (DUPLICATE_CLAIM, LETTER),
        (DUPLICATE_CLAIM, NOTE),
        (DENIED_CLAIM, LETTER),
        (DENIED_CLAIM, NOTE),
        (DENIED_CLAIM, PRIOR_AUTH),
    ]
    assert {row.seed for row in rows} == {SEED}
    for row in rows:
        assert row.generator_version == GENERATOR_VERSIONS[row.document_type]
        assert row.template_id in TEMPLATE_IDS[row.document_type]


@pytest.mark.parametrize("document_type", list(DocumentType))
def test_a_row_is_the_generators_document_for_the_stored_claim_lines_and_label(
    session: Session, document_type: DocumentType
) -> None:
    _store_all(session)
    expected = GENERATORS[document_type](
        Claim(DENIED_CLAIM, FROM_DATE, THRU_DATE, DIAGNOSIS_CODES),
        DENIED_CLAIM_LINES,
        ClaimLabel(
            source_claim_id=DENIED_CLAIM,
            is_denied=True,
            denial_reason_category=NONCOVERED,
            appeal_success_proxy=False,
            label_rule_version=LABEL_RULE_VERSION,
        ),
        SEED,
    )

    row = _built(session, DENIED_CLAIM, document_type)

    assert row.text == expected.text
    assert row.template_id == expected.template_id
    assert row.answer_key == expected.answer_key.model_dump(mode="json")


def test_a_claims_documents_use_only_that_claims_lines(session: Session) -> None:
    _store_all(session)

    letter = _built(session, DENIED_CLAIM, LETTER).answer_key
    note = _built(session, DENIED_CLAIM, NOTE).answer_key
    record = _built(session, DENIED_CLAIM, PRIOR_AUTH).answer_key

    # The totals are this claim's two lines, not the other claims'.
    assert letter["total_allowed_charge_amount"] == "100.00"
    assert [line["line_number"] for line in letter["denied_lines"]] == [2]
    assert note["procedure_codes"] == ["71020", "99213"]  # both lines, in line order
    assert record["requested_procedure_codes"] == ["99213"]  # the denied line only


def test_a_claims_documents_share_one_made_up_identity(session: Session) -> None:
    _store_all(session)

    keys = [_built(session, DENIED_CLAIM, kind).answer_key for kind in DocumentType]

    for field in ("patient_name", "member_id", "provider_name"):
        assert len({key[field] for key in keys}) == 1


def test_same_seed_gives_identical_rows_and_another_seed_gives_other_text(
    session: Session,
) -> None:
    _store_all(session)

    first = build_document_rows(session, SEED)

    assert build_document_rows(session, SEED) == first
    other = build_document_rows(session, SEED + 1)
    assert [row.text for row in other] != [row.text for row in first]
    assert {row.seed for row in other} == {SEED + 1}


def test_building_writes_nothing(session: Session) -> None:
    _store_all(session)

    build_document_rows(session, SEED)

    assert _stored(session) == {}


def test_counts_every_document_type_in_build_order(session: Session) -> None:
    _store_all(session)

    counts = count_by_type(build_document_rows(session, SEED))

    assert list(counts.items()) == [(LETTER, 3), (NOTE, 3), (PRIOR_AUTH, 2)]


def test_a_type_without_documents_is_counted_as_zero() -> None:
    assert count_by_type([_row()]) == {LETTER: 1, NOTE: 0, PRIOR_AUTH: 0}
    assert count_by_type([]) == {LETTER: 0, NOTE: 0, PRIOR_AUTH: 0}


def test_built_rows_say_that_no_noise_was_applied_yet(session: Session) -> None:
    _store_all(session)

    rows = build_document_rows(session, SEED)

    assert {row.noise_version for row in rows} == {NOISE_NOT_APPLIED_VERSION}
    assert all(row.noise_record == NOISE_NOT_APPLIED_RECORD for row in rows)


def test_every_document_type_has_a_generator_version() -> None:
    assert GENERATOR_VERSIONS == {
        LETTER: denial_letter.GENERATOR_VERSION,
        NOTE: clinical_note.GENERATOR_VERSION,
        PRIOR_AUTH: prior_auth.GENERATOR_VERSION,
    }
    assert set(GENERATOR_VERSIONS) == set(DocumentType)


def test_rejects_when_no_claim_is_labelled_denied(session: Session) -> None:
    _store_claim(session, PAID_CLAIM, PAID_CLAIM_LINES, is_denied=False)

    with pytest.raises(BatchRejectedError, match="no claim is labelled denied"):
        build_document_rows(session, SEED)


def test_rejects_labels_made_by_another_rule_version(session: Session) -> None:
    _store_claim(session, DENIED_CLAIM, DENIED_CLAIM_LINES, is_denied=True)
    _store_claim(
        session, OTHER_DENIED_CLAIM, OTHER_DENIED_CLAIM_LINES, is_denied=True, rule_version="v0"
    )

    with pytest.raises(BatchRejectedError, match="rule version v0, the code is at v1"):
        build_document_rows(session, SEED)


@pytest.mark.parametrize("lines", [PAID_CLAIM_LINES, ()], ids=["only a paid line", "no lines"])
def test_rejects_a_denied_claim_whose_stored_lines_hold_no_denied_line(
    session: Session, lines: tuple[Line, ...]
) -> None:
    _store_claim(session, DENIED_CLAIM, DENIED_CLAIM_LINES, is_denied=True)
    # Labelled denied, but no stored line is denied: the lines changed after the label.
    _store_claim(session, OTHER_DENIED_CLAIM, lines, is_denied=True)

    with pytest.raises(BatchRejectedError) as excinfo:
        build_document_rows(session, SEED)

    assert excinfo.value.problems == [
        f"claim {OTHER_DENIED_CLAIM} is labelled denied but has no denied line: run"
        " pipelines.run_data_pipeline again"
    ]


def test_rejects_a_claim_labelled_for_a_record_whose_stored_lines_hold_no_line_that_qualifies(
    session: Session,
) -> None:
    _store_claim(session, DENIED_CLAIM, DENIED_CLAIM_LINES, is_denied=True)
    # The headline says noncovered, but the only denied line is a duplicate: the lines changed
    # after the label.
    _store_claim(session, OTHER_DENIED_CLAIM, DUPLICATE_CLAIM_LINES, is_denied=True)

    with pytest.raises(BatchRejectedError) as excinfo:
        build_document_rows(session, SEED)

    assert excinfo.value.problems == [
        f"claim {OTHER_DENIED_CLAIM} is labelled for a prior-authorisation record but has no"
        " line that qualifies: run pipelines.run_data_pipeline again"
    ]


@pytest.mark.parametrize("seed", [-1, MAX_SEED + 1])
def test_rejects_a_seed_that_does_not_fit_the_integer_column(session: Session, seed: int) -> None:
    _store_all(session)

    with pytest.raises(ValueError, match="seed must be from 0 to"):
        build_document_rows(session, seed)


@pytest.mark.parametrize("seed", [-1, MAX_SEED + 1])
def test_row_model_rejects_a_seed_outside_the_integer_column(seed: int) -> None:
    with pytest.raises(ValidationError, match="seed"):
        _row(seed=seed)


def test_replace_stores_the_rows_and_the_answer_key_reads_back(session: Session) -> None:
    _store_all(session)
    rows = build_document_rows(session, SEED)

    written = replace_generated_document_rows(session, rows)

    stored = _stored(session)
    assert written == 8
    assert set(stored) == {(row.source_claim_id, row.document_type) for row in rows}
    document = stored[DENIED_CLAIM, LETTER]
    assert (document.seed, document.generator_version) == (SEED, GENERATOR_VERSION)
    assert document.text == rows[5].text
    # The stored JSON turns back into the typed answer key, with real dates and amounts.
    key = DenialLetterAnswerKey.model_validate(document.answer_key)
    assert key.claim_number == DENIED_CLAIM
    assert (key.service_from_date, key.service_thru_date) == (FROM_DATE, THRU_DATE)
    assert key.total_payment_amount == Decimal("80.00")
    assert key.model_dump(mode="json") == rows[5].answer_key


def test_every_stored_answer_key_reads_back_into_the_model_of_its_type(session: Session) -> None:
    _store_all(session)
    rows = build_document_rows(session, SEED)

    replace_generated_document_rows(session, rows)

    stored = _stored(session)
    for row in rows:
        document = stored[row.source_claim_id, row.document_type]
        key = ANSWER_KEY_MODELS[row.document_type].model_validate(document.answer_key)
        assert key.model_dump(mode="json") == row.answer_key
        assert document.text == row.text


def test_stored_answer_key_has_exactly_the_fields_of_the_answer_key_model(
    session: Session,
) -> None:
    _store_all(session)

    replace_generated_document_rows(session, build_document_rows(session, SEED))

    for (_, document_type), document in _stored(session).items():
        assert set(document.answer_key) == set(ANSWER_KEY_MODELS[document_type].model_fields)
        for line in document.answer_key.get("denied_lines", []):
            assert set(line) == set(DeniedLineKey.model_fields)


def test_replace_leaves_only_the_given_rows(session: Session) -> None:
    _store_all(session)
    replace_generated_document_rows(session, build_document_rows(session, SEED))

    # A later run in which only one claim is still denied, with another seed.
    written = replace_generated_document_rows(session, [_row(DENIED_CLAIM, seed=7)])

    # The notes and records of the earlier run are gone too, not only its letters.
    stored = _stored(session)
    assert written == 1
    assert set(stored) == {(DENIED_CLAIM, LETTER)}
    assert stored[DENIED_CLAIM, LETTER].seed == 7


def test_replace_with_no_rows_empties_the_table(session: Session) -> None:
    _store_all(session)
    replace_generated_document_rows(session, build_document_rows(session, SEED))

    assert replace_generated_document_rows(session, []) == 0

    assert _stored(session) == {}


def test_replace_keeps_the_claims_and_their_labels(session: Session) -> None:
    _store_all(session)

    replace_generated_document_rows(session, build_document_rows(session, SEED))

    assert session.scalar(select(func.count()).select_from(ClaimSample)) == 4
    assert session.scalar(select(func.count()).select_from(ClaimSampleLabel)) == 4


def test_replace_rejects_a_document_whose_claim_is_not_stored(session: Session) -> None:
    with pytest.raises(IntegrityError, match="fk_generated_documents_claim"):
        replace_generated_document_rows(session, [_row()])
