"""Counts how often the rules give an extracted letter the verdict its answer key gets.

The deterministic rules (`src/rules`) read three values of a denial letter. Here they run
twice per letter: on the answer key's values (the expected verdict) and on the values the
model extracted. The count says how much a reading mistake changes what happens to a claim.
It is reported, not a gate: the build plan has no target for it. Everything here is
generated data.
"""

from collections import Counter
from collections.abc import Iterable, Mapping
from datetime import date
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from src.db.models import DocumentType
from src.evaluation.extraction_accuracy import MatchCount
from src.extraction.schemas import DenialLetterExtraction
from src.extraction.store import DocumentExtractionRow, DocumentKey, StoredDocument, text_sha256
from src.rules.evaluate import evaluate_rules
from src.rules.verdict import RuleInputs, RuleOutcome
from src.synth.denial_letter import DenialLetterAnswerKey


class OutcomeMove(BaseModel):
    """How many letters got `extracted` from the extracted values where `expected` was due."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    expected: RuleOutcome
    extracted: RuleOutcome
    letters: int = Field(ge=1)


class RulesAgreementReport(BaseModel):
    """The rule verdicts of the extracted denial letters against those of the answer keys.

    A letter has no verdict from extraction when it has no current result (none, or one made
    from another text) or when the extracted amount is negative, which the rules refuse.
    `expected_outcomes` counts the answer-key verdict of every letter, with or without a
    result; every outcome is listed. `same_outcome` compares the letters with a verdict.
    `moves` lists the pairs that differ, in `RuleOutcome` order.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    letters: int = Field(ge=0)
    with_verdict: int = Field(ge=0)
    no_verdict: int = Field(ge=0)
    expected_outcomes: dict[RuleOutcome, int]
    same_outcome: MatchCount
    moves: tuple[OutcomeMove, ...]


def build_rules_agreement_report(
    documents: Iterable[StoredDocument],
    rows: Mapping[DocumentKey, DocumentExtractionRow],
    *,
    today: date,
    amount_floor: Decimal,
) -> RulesAgreementReport:
    """Run the rules on each denial letter's answer key and on its extracted values.

    Only denial letters are read; other documents are ignored, and so is a row for a letter
    that is not in `documents`. A value the noise step blanked is missing in the expected
    verdict too, because it is not in the text. `today` and `amount_floor` are passed on to
    `evaluate_rules` as they are.
    """
    letters = with_verdict = same = 0
    expected_outcomes = dict.fromkeys(RuleOutcome, 0)
    moved: Counter[tuple[RuleOutcome, RuleOutcome]] = Counter()

    for document in documents:
        if document.document_type is not DocumentType.DENIAL_LETTER:
            continue
        letters += 1
        expected = evaluate_rules(
            _expected_inputs(document), today=today, amount_floor=amount_floor
        ).outcome
        expected_outcomes[expected] += 1

        row = rows.get(document.key)
        if row is None or row.text_sha256 != text_sha256(document.text):
            continue
        inputs = _extracted_inputs(row)
        if inputs is None:
            continue
        extracted = evaluate_rules(inputs, today=today, amount_floor=amount_floor).outcome
        with_verdict += 1
        if extracted is expected:
            same += 1
        else:
            moved[expected, extracted] += 1

    return RulesAgreementReport(
        letters=letters,
        with_verdict=with_verdict,
        no_verdict=letters - with_verdict,
        expected_outcomes=expected_outcomes,
        same_outcome=MatchCount(matched=same, compared=with_verdict),
        moves=tuple(
            OutcomeMove(expected=expected, extracted=extracted, letters=moved[expected, extracted])
            for expected in RuleOutcome
            for extracted in RuleOutcome
            if moved[expected, extracted]
        ),
    )


def _expected_inputs(document: StoredDocument) -> RuleInputs:
    answer_key = DenialLetterAnswerKey.model_validate(document.answer_key)
    blanked = document.missing_field
    return RuleInputs(
        letter_date=None if blanked == "letter_date" else answer_key.letter_date,
        appeal_deadline=None if blanked == "appeal_deadline" else answer_key.appeal_deadline,
        total_allowed_charge_amount=(
            None
            if blanked == "total_allowed_charge_amount"
            else answer_key.total_allowed_charge_amount
        ),
    )


def _extracted_inputs(row: DocumentExtractionRow) -> RuleInputs | None:
    """The rule inputs of one result, or None when the rules would refuse its amount."""
    extraction = DenialLetterExtraction.model_validate(row.fields)
    amount = extraction.total_allowed_charge_amount.value
    if amount is not None and amount < 0:
        return None
    return RuleInputs(
        letter_date=extraction.letter_date.value,
        appeal_deadline=extraction.appeal_deadline.value,
        total_allowed_charge_amount=amount,
    )
