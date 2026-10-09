from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError

from src.db.models import DatasetSplit
from src.evaluation.extraction_accuracy import ExtractionAccuracyReport, MatchCount
from src.evaluation.model_score import ModelScore
from src.evaluation.report import EvaluationReport, GateStatus, build_evaluation_report
from src.evaluation.rules_agreement import RulesAgreementReport
from tests.evaluation.helpers import by_key, result_row, stored_document
from tests.extraction.helpers import CLAIM_1, CLAIM_2, LETTER, NOTE, PATIENT_NAME, PROMPT_VERSION

TEST = DatasetSplit.TEST
MET = GateStatus.MET
BELOW = GateStatus.BELOW
NOT_MEASURED = GateStatus.NOT_MEASURED
# The made-up letter's deadline is 2008-09-16, so on this day its answer key passes the rules.
OPEN_DAY = date(2008, 6, 1)
FLOOR = Decimal("25")
NO_MODEL_FILE = "the facts file win_model.json is missing"


def _count(matched: int, compared: int) -> MatchCount:
    return MatchCount(matched=matched, compared=compared)


def _extraction(
    accuracy: tuple[int, int] = (90, 100), headline: tuple[int, int] = (9, 10)
) -> ExtractionAccuracyReport:
    return ExtractionAccuracyReport(
        documents=10,
        with_result=10,
        no_result=0,
        stale=0,
        with_result_only=_count(*accuracy),
        all_documents=_count(*accuracy),
        by_type={},
        by_noise_level={},
        headline_reason=_count(*headline),
        by_confidence={},
    )


def _model(auc: float | None = 0.75) -> ModelScore:
    return ModelScore(split=TEST, rows=10, proxy_true=5, auc=auc, best_possible_auc=auc)


def _report(
    extraction: ExtractionAccuracyReport | None = None,
    *,
    model: ModelScore | None = None,
    model_problem: str | None = None,
) -> EvaluationReport:
    if model is None and model_problem is None:
        model = _model()
    return EvaluationReport(
        split=TEST,
        prompt_version=PROMPT_VERSION,
        rules_today=OPEN_DAY,
        amount_floor=FLOOR,
        extraction=extraction or _extraction(),
        rules=RulesAgreementReport(
            letters=0,
            with_verdict=0,
            no_verdict=0,
            expected_outcomes={},
            same_outcome=_count(0, 0),
            moves=(),
        ),
        model=model,
        model_problem=model_problem,
    )


def _statuses(report: EvaluationReport) -> tuple[GateStatus, GateStatus, GateStatus]:
    return (
        report.field_accuracy_status,
        report.headline_reason_status,
        report.model_auc_status,
    )


def test_targets_are_met_when_all_three_numbers_are_above_them() -> None:
    report = _report()

    assert _statuses(report) == (MET, MET, MET)
    assert report.targets_met
    assert report.as_text().splitlines()[-1] == "Stage 3 targets met: yes"


def test_a_number_exactly_at_its_target_meets_it() -> None:
    report = _report(_extraction(accuracy=(85, 100), headline=(17, 20)), model=_model(0.70))

    assert _statuses(report) == (MET, MET, MET)
    assert report.targets_met


@pytest.mark.parametrize(
    ("report", "statuses"),
    [
        (_report(_extraction(accuracy=(8499, 10000))), (BELOW, MET, MET)),
        (_report(_extraction(headline=(8499, 10000))), (MET, BELOW, MET)),
        (_report(model=_model(0.6999)), (MET, MET, BELOW)),
    ],
    ids=["field accuracy", "headline reason", "model AUC"],
)
def test_one_number_just_below_its_target_is_enough_to_miss(
    report: EvaluationReport, statuses: tuple[GateStatus, GateStatus, GateStatus]
) -> None:
    assert _statuses(report) == statuses
    assert not report.targets_met
    assert report.as_text().splitlines()[-1] == "Stage 3 targets met: no"


def test_nothing_compared_cannot_be_measured_and_misses() -> None:
    report = _report(_extraction(accuracy=(0, 0), headline=(0, 0)))

    assert _statuses(report) == (NOT_MEASURED, NOT_MEASURED, MET)
    assert not report.targets_met
    lines = report.as_text().splitlines()
    assert "  field accuracy, at least 85%: cannot be measured" in lines
    assert "  headline denial reason, at least 85%: cannot be measured" in lines


def test_a_split_with_one_kind_of_proxy_has_no_auc_and_misses() -> None:
    report = _report(model=_model(None))

    assert report.model_auc is None
    assert report.model_auc_status is NOT_MEASURED
    assert not report.targets_met
    text = report.as_text()
    assert "model AUC n/a | best possible AUC n/a" in text
    assert "  model AUC, at least 0.70: cannot be measured" in text.splitlines()


def test_a_model_that_could_not_be_scored_is_named_with_its_reason_and_misses() -> None:
    report = _report(model_problem=NO_MODEL_FILE)

    assert report.model_auc_status is NOT_MEASURED
    assert not report.targets_met
    lines = report.as_text().splitlines()
    assert f"win-probability model: cannot be measured ({NO_MODEL_FILE})" in lines
    # The extraction half is still reported.
    assert "  field accuracy, at least 85%: 90.00%, meets the target" in lines


def test_a_report_needs_a_model_score_or_a_reason_and_not_both() -> None:
    values = _report().model_dump()

    with pytest.raises(ValidationError, match="exactly one of model and model_problem"):
        EvaluationReport(**{**values, "model_problem": NO_MODEL_FILE})
    with pytest.raises(ValidationError, match="exactly one of model and model_problem"):
        EvaluationReport(**{**values, "model": None})


def test_a_number_below_its_target_is_never_printed_as_the_target() -> None:
    report = _report(
        _extraction(accuracy=(84996, 100000), headline=(84996, 100000)), model=_model(0.69996)
    )

    lines = report.as_text().splitlines()

    assert "  field accuracy, at least 85%: 84.99%, below the target" in lines
    assert "  headline denial reason, at least 85%: 84.99%, below the target" in lines
    assert "  model AUC, at least 0.70: 0.6999, below the target" in lines


def test_other_numbers_are_rounded_to_the_nearest_step() -> None:
    report = _report(_extraction(accuracy=(20317, 21837)), model=_model(0.75386))

    lines = report.as_text().splitlines()

    # 93.0393...% and not 93.03%.
    assert "  field accuracy, at least 85%: 93.04%, meets the target" in lines
    assert "  model AUC, at least 0.70: 0.7539, meets the target" in lines


def test_the_report_cannot_be_changed() -> None:
    report = _report()

    with pytest.raises(ValidationError):
        report.split = DatasetSplit.TRAIN


def _built() -> EvaluationReport:
    """Two letters and a note: one letter has no result, the other a deadline read as missing."""
    letter, note, unread = (
        stored_document(LETTER),
        stored_document(NOTE),
        stored_document(LETTER, CLAIM_2),
    )
    rows = by_key(result_row(letter, appeal_deadline=None), result_row(note))
    return build_evaluation_report(
        # A generator: the documents are read by two measurements.
        (document for document in (letter, note, unread)),
        rows,
        split=TEST,
        prompt_version=PROMPT_VERSION,
        rules_today=OPEN_DAY,
        amount_floor=FLOOR,
        model=_model(0.75),
        model_problem=None,
    )


def test_the_built_report_holds_both_measurements_of_the_same_documents() -> None:
    report = _built()

    assert (report.extraction.documents, report.extraction.no_result) == (3, 1)
    assert report.extraction.all_documents == _count(22, 38)
    assert report.extraction.headline_reason == _count(1, 2)
    assert (report.rules.letters, report.rules.no_verdict) == (2, 1)
    assert report.rules.same_outcome == _count(0, 1)
    assert (report.field_accuracy_status, report.headline_reason_status) == (BELOW, BELOW)
    assert not report.targets_met


def test_the_text_says_the_data_is_generated_and_names_what_was_used() -> None:
    lines = _built().as_text().splitlines()

    assert lines[0].startswith("Stage 3 evaluation on the test split (generated data:")
    assert "proxy, not an observed outcome" in lines[0]
    assert lines[1] == (
        f"extraction prompt {PROMPT_VERSION} | win-probability model v1"
        " | rules v1, today 2008-06-01, amount floor $25"
    )


def test_the_text_lists_every_count_of_the_report() -> None:
    lines = _built().as_text().splitlines()

    for line in (
        "documents: 3 | with a current result 2 | no result 1 | stale result 0",
        "  no result counted as wrong (the number checked against the target):"
        " 22 of 38 (57.89%)",
        "  no result left out: 22 of 23 (95.65%)",
        "    denial_letter (2 documents, 1 with a result): 14 of 30 (46.67%)",
        "      appeal_deadline: 0 of 1 (0.00%)",
        "      claim_number: 1 of 1 (100.00%)",
        "    clinical_note (1 documents, 1 with a result): 8 of 8 (100.00%)",
        "    prior_auth (0 documents, 0 with a result): 0 of 0 (n/a)",
        "    none: 22 of 23 (95.65%)",
        "    heavy: 0 of 0 (n/a)",
        "    0.90 to 0.94: 22 of 23 (95.65%)",
        "headline denial reason (denial letters, no result counted as wrong): 1 of 2 (50.00%)",
        "win-probability model (saved model, not trained here): 10 denied claims"
        " | proxy true 5 (50.00%) | model AUC 0.7500 | best possible AUC 0.7500",
        "  denial letters: 2 | with a verdict from extraction 1 | no verdict 1",
        "  answer-key outcomes: pass 2 | must_review 0 | hard_fail 0",
        "  same outcome: 0 of 1 (0.00%)",
        "  pass to must_review: 1",
        "  field accuracy, at least 85%: 57.89%, below the target",
        "  headline denial reason, at least 85%: 50.00%, below the target",
        "  model AUC, at least 0.70: 0.7500, meets the target",
        "Stage 3 targets met: no",
    ):
        assert line in lines


def test_the_text_holds_no_document_value() -> None:
    text = _built().as_text()

    assert PATIENT_NAME not in text
    assert CLAIM_1 not in text
    assert "MBR-000001" not in text
