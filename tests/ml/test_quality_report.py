import pytest

from src.db.models import DatasetSplit, DenialReasonCategory
from src.ml.claim_labels import ClaimLabelRow
from src.ml.quality_report import LabelCounts, LabelQualityReport, build_label_quality_report


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


def _report(denied: int, proxy_true: int, claims: int = 100) -> LabelQualityReport:
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
    )


ROWS = [
    _row(1),
    _row(2, proxy=True),
    _row(3, proxy=False),
    _row(4, DatasetSplit.VALIDATION, proxy=False),
    _row(5, DatasetSplit.VALIDATION),
    _row(6, DatasetSplit.TEST, proxy=True),
]


def test_counts_claims_denied_and_true_proxies_per_split() -> None:
    report = build_label_quality_report(ROWS)

    assert report.label_rule_version == "v1"
    assert report.split_seed == 42
    assert report.splits == {
        DatasetSplit.TRAIN: LabelCounts(claims=3, denied=2, proxy_true=1),
        DatasetSplit.VALIDATION: LabelCounts(claims=2, denied=1, proxy_true=0),
        DatasetSplit.TEST: LabelCounts(claims=1, denied=1, proxy_true=1),
    }
    assert report.total == LabelCounts(claims=6, denied=4, proxy_true=2)


def test_an_empty_split_is_reported_with_zero_counts() -> None:
    report = build_label_quality_report(ROWS[:3])

    assert report.splits[DatasetSplit.TEST] == LabelCounts(claims=0, denied=0, proxy_true=0)


@pytest.mark.parametrize(
    "rows",
    [[], [_row(1), _row(2, version="v2")], [_row(1), _row(2, seed=7)]],
    ids=["no rows", "two rule versions", "two seeds"],
)
def test_rejects_rows_that_are_not_from_one_run(rows: list[ClaimLabelRow]) -> None:
    with pytest.raises(ValueError, match="exactly one rule version and one split seed"):
        build_label_quality_report(rows)


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
    text = build_label_quality_report(ROWS).as_text()

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
    text = _report(denied=5325, proxy_true=1888, claims=50000).as_text()

    assert "train: 50,000 (100.00%) | 5,325 (10.65%) | 1,888 (35.46%)" in text
