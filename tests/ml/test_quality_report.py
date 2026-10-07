from decimal import Decimal

import pytest

from src.db.models import DatasetSplit, DenialReasonCategory
from src.ml.claim_labels import ClaimLabelRow
from src.ml.quality_report import (
    LabelCounts,
    LabelQualityReport,
    best_possible_auc,
    build_label_quality_report,
)


def _row(
    number: int,
    split: DatasetSplit = DatasetSplit.TRAIN,
    proxy: bool | None = None,
    version: str = "v1",
    seed: int = 42,
) -> ClaimLabelRow:
    """A made-up label row. `proxy` None means the claim is not denied."""
    denied = proxy is not None
    return ClaimLabelRow(
        source_claim_id=f"{800000000000000 + number}",
        is_denied=denied,
        denial_reason_category=DenialReasonCategory.OTHER if denied else None,
        appeal_success_proxy=proxy,
        label_rule_version=version,
        split=split,
        split_seed=seed,
    )


def _claim_id(number: int) -> str:
    return f"{800000000000000 + number}"


def _scores(*values: str) -> list[Decimal]:
    return [Decimal(value) for value in values]


def _report(
    denied: int, proxy_true: int, claims: int = 100, best_auc: float | None = None
) -> LabelQualityReport:
    """A report with everything in the train split."""
    empty = LabelCounts()
    return LabelQualityReport(
        label_rule_version="v1",
        split_seed=42,
        splits={
            DatasetSplit.TRAIN: LabelCounts(claims=claims, denied=denied, proxy_true=proxy_true),
            DatasetSplit.VALIDATION: empty,
            DatasetSplit.TEST: empty,
        },
        best_auc_by_split={
            DatasetSplit.TRAIN: best_auc,
            DatasetSplit.VALIDATION: None,
            DatasetSplit.TEST: None,
        },
        best_auc_total=best_auc,
    )


ROWS = [
    _row(1),
    _row(2, proxy=True),
    _row(3, proxy=False),
    _row(4, DatasetSplit.VALIDATION, proxy=False),
    _row(5, DatasetSplit.VALIDATION),
    _row(6, DatasetSplit.TEST, proxy=True),
]
# One chance per denied claim of `ROWS`.
CHANCES = {
    _claim_id(2): Decimal("0.80"),
    _claim_id(3): Decimal("0.30"),
    _claim_id(4): Decimal("0.20"),
    _claim_id(6): Decimal("0.20"),
}


def test_counts_claims_denied_and_true_proxies_per_split() -> None:
    report = build_label_quality_report(ROWS, CHANCES)

    assert report.label_rule_version == "v1"
    assert report.split_seed == 42
    assert report.splits == {
        DatasetSplit.TRAIN: LabelCounts(claims=3, denied=2, proxy_true=1),
        DatasetSplit.VALIDATION: LabelCounts(claims=2, denied=1, proxy_true=0),
        DatasetSplit.TEST: LabelCounts(claims=1, denied=1, proxy_true=1),
    }
    assert report.total == LabelCounts(claims=6, denied=4, proxy_true=2)


def test_an_empty_split_is_reported_with_zero_counts() -> None:
    report = build_label_quality_report(ROWS[:3], CHANCES)

    assert report.splits[DatasetSplit.TEST] == LabelCounts(claims=0, denied=0, proxy_true=0)


@pytest.mark.parametrize(
    "rows",
    [[], [_row(1), _row(2, version="v2")], [_row(1), _row(2, seed=7)]],
    ids=["no rows", "two rule versions", "two seeds"],
)
def test_rejects_rows_that_are_not_from_one_run(rows: list[ClaimLabelRow]) -> None:
    with pytest.raises(ValueError, match="exactly one rule version and one split seed"):
        build_label_quality_report(rows, {})


@pytest.mark.parametrize(("denied", "proxy_true"), [(10, 2), (10, 5), (10, 8), (3, 1)])
def test_gate_passes_when_20_to_80_percent_of_denied_claims_have_a_true_proxy(
    denied: int, proxy_true: int
) -> None:
    assert _report(denied, proxy_true).problems() == []


@pytest.mark.parametrize(
    ("denied", "proxy_true", "share"),
    [(1000, 199, "19.90%"), (1000, 801, "80.10%"), (2, 0, "0.00%"), (2, 2, "100.00%")],
)
def test_gate_fails_when_the_true_share_is_outside_20_to_80_percent(
    denied: int, proxy_true: int, share: str
) -> None:
    (problem,) = _report(denied, proxy_true).problems()

    assert f"true for {share} of denied claims, outside 20% to 80%" in problem
    assert "proxy" in problem


def test_gate_fails_when_no_claim_is_denied() -> None:
    assert _report(denied=0, proxy_true=0).problems() == [
        "no claim is denied, so there is no appeal-success proxy to balance"
    ]


def test_text_gives_the_version_seed_counts_and_shares() -> None:
    text = build_label_quality_report(ROWS, CHANCES).as_text()

    assert "label rule version: v1" in text
    assert "split seed: 42" in text
    assert "claims labelled: 6" in text
    assert "train: 3 (50.00%) | 2 (66.67%) | 1 (50.00%)" in text
    assert "validation: 2 (33.33%) | 1 (50.00%) | 0 (0.00%)" in text
    assert "test: 1 (16.67%) | 1 (100.00%) | 1 (100.00%)" in text
    assert "all: 6 (100.00%) | 4 (66.67%) | 2 (50.00%)" in text
    assert text.endswith("20% to 80%): 50.00%, passes")


def test_text_calls_the_label_a_proxy_and_shows_a_failed_gate() -> None:
    text = _report(denied=2, proxy_true=0, claims=6).as_text()

    assert "the appeal-success label is a proxy, not an observed outcome" in text
    assert text.endswith("0.00%, fails")


def test_text_shows_no_share_where_there_is_nothing_to_divide_by() -> None:
    text = _report(denied=0, proxy_true=0, claims=4).as_text()

    assert "validation: 0 (0.00%) | 0 (n/a) | 0 (n/a)" in text
    assert "train: 4 (100.00%) | 0 (0.00%) | 0 (n/a)" in text


def test_text_separates_thousands() -> None:
    text = _report(denied=5325, proxy_true=2813, claims=50000).as_text()

    assert "train: 50,000 (100.00%) | 5,325 (10.65%) | 2,813 (52.83%)" in text


def test_auc_is_one_when_every_true_claim_scores_above_every_false_one() -> None:
    assert best_possible_auc(_scores("0.80", "0.60"), _scores("0.50", "0.10")) == 1.0


def test_auc_is_zero_when_every_true_claim_scores_below_every_false_one() -> None:
    assert best_possible_auc(_scores("0.10"), _scores("0.50", "0.80")) == 0.0


def test_auc_is_a_half_when_all_scores_are_equal() -> None:
    assert best_possible_auc(_scores("0.45", "0.45"), _scores("0.45", "0.45", "0.45")) == 0.5


def test_auc_counts_a_pair_with_equal_scores_as_half_a_win() -> None:
    # Six pairs. True 0.80 beats all three false scores: 3. True 0.35 beats 0.20, ties with
    # 0.35 and loses to 0.70: 1.5. So 4.5 of 6.
    auc = best_possible_auc(_scores("0.80", "0.35"), _scores("0.20", "0.35", "0.70"))

    assert auc == 0.75


def test_auc_counts_every_claim_when_scores_repeat() -> None:
    # Four pairs: 0.80 beats 0.20 twice, 0.20 ties with 0.20 twice: (2 + 1) of 4.
    assert best_possible_auc(_scores("0.80", "0.20"), _scores("0.20", "0.20")) == 0.75


@pytest.mark.parametrize(
    ("true_scores", "false_scores"),
    [(_scores(), _scores("0.50")), (_scores("0.50"), _scores()), (_scores(), _scores())],
    ids=["no true proxy", "no false proxy", "no denied claim"],
)
def test_there_is_no_auc_without_both_a_true_and_a_false_proxy(
    true_scores: list[Decimal], false_scores: list[Decimal]
) -> None:
    assert best_possible_auc(true_scores, false_scores) is None


def test_report_gives_the_best_possible_auc_per_split_and_for_all_claims() -> None:
    report = build_label_quality_report(ROWS, CHANCES)

    # Train: true 0.80 above false 0.30. Validation has no true proxy, test no false one.
    assert report.best_auc_by_split == {
        DatasetSplit.TRAIN: 1.0,
        DatasetSplit.VALIDATION: None,
        DatasetSplit.TEST: None,
    }
    # All: true 0.80 and 0.20 against false 0.30 and 0.20: (2 + 0.5) of 4 pairs.
    assert report.best_auc_total == 0.625


def test_best_possible_auc_ignores_the_chance_of_a_claim_outside_the_rows() -> None:
    extra = CHANCES | {_claim_id(99): Decimal("0.02")}

    assert build_label_quality_report(ROWS, extra) == build_label_quality_report(ROWS, CHANCES)


def test_rejects_a_denied_claim_without_a_chance() -> None:
    chances = {claim_id: chance for claim_id, chance in CHANCES.items() if claim_id != _claim_id(4)}

    with pytest.raises(ValueError, match="denied claim 800000000000004 has no appeal-success"):
        build_label_quality_report(ROWS, chances)


def test_text_gives_the_best_possible_auc_beside_the_target() -> None:
    text = build_label_quality_report(ROWS, CHANCES).as_text()

    assert (
        "best possible AUC (score = the rule's own chance; target 0.70; reported, not a gate):"
        " train 1.0000 | validation n/a | test n/a | all 0.6250"
    ) in text


def test_a_best_possible_auc_below_the_target_does_not_fail_the_run() -> None:
    report = _report(denied=10, proxy_true=5, best_auc=0.55)

    assert report.problems() == []
    assert "train 0.5500" in report.as_text()
