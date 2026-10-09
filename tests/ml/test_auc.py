from decimal import Decimal

import pytest

from src.ml.auc import TARGET_AUC, pairwise_auc


def _scores(*values: str) -> list[Decimal]:
    return [Decimal(value) for value in values]


def test_auc_is_one_when_every_true_claim_scores_above_every_false_one() -> None:
    assert pairwise_auc(_scores("0.80", "0.60"), _scores("0.50", "0.10")) == 1.0


def test_auc_is_zero_when_every_true_claim_scores_below_every_false_one() -> None:
    assert pairwise_auc(_scores("0.10"), _scores("0.50", "0.80")) == 0.0


def test_auc_is_a_half_when_all_scores_are_equal() -> None:
    assert pairwise_auc(_scores("0.45", "0.45"), _scores("0.45", "0.45", "0.45")) == 0.5


def test_auc_counts_a_pair_with_equal_scores_as_half_a_win() -> None:
    # Six pairs. True 0.80 beats all three false scores: 3. True 0.35 beats 0.20, ties with
    # 0.35 and loses to 0.70: 1.5. So 4.5 of 6.
    auc = pairwise_auc(_scores("0.80", "0.35"), _scores("0.20", "0.35", "0.70"))

    assert auc == 0.75


def test_auc_counts_every_claim_when_scores_repeat() -> None:
    # Four pairs: 0.80 beats 0.20 twice, 0.20 ties with 0.20 twice: (2 + 1) of 4.
    assert pairwise_auc(_scores("0.80", "0.20"), _scores("0.20", "0.20")) == 0.75


@pytest.mark.parametrize(
    ("true_scores", "false_scores"),
    [(_scores(), _scores("0.50")), (_scores("0.50"), _scores()), (_scores(), _scores())],
    ids=["no true proxy", "no false proxy", "no denied claim"],
)
def test_there_is_no_auc_without_both_a_true_and_a_false_proxy(
    true_scores: list[Decimal], false_scores: list[Decimal]
) -> None:
    assert pairwise_auc(true_scores, false_scores) is None


def test_auc_scores_a_models_floats_the_same_way_as_exact_decimals() -> None:
    assert pairwise_auc([0.8, 0.35], [0.2, 0.35, 0.7]) == 0.75


def test_the_target_is_the_stage_3_done_condition() -> None:
    assert Decimal("0.70") == TARGET_AUC
