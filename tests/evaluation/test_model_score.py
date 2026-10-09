from decimal import Decimal
from typing import Any

import pytest
from pydantic import ValidationError

from src.db.models import DatasetSplit, DenialReasonCategory
from src.evaluation.model_score import ModelScore, score_saved_model
from src.ml.training import WinModel
from src.ml.win_model_run import DeniedClaim, TrainingData, train_and_score
from tests.ml.made_up_rows import features, training_rows

TRAIN = DatasetSplit.TRAIN
VALIDATION = DatasetSplit.VALIDATION
TEST = DatasetSplit.TEST
SPLIT_SEED = 42
SEED = 7


class _AmountClassifier:
    """A stand-in for a fitted model: the chance is the claim's amount divided by 1,000."""

    def predict_proba(self, matrix: Any) -> list[list[float]]:
        return [[1 - row[1] / 1000, row[1] / 1000] for row in matrix]


AMOUNT_MODEL = WinModel(classifier=_AmountClassifier(), training_seed=SEED)


def _claim(split: DatasetSplit, amount: str, proxy: bool, chance: str = "0.50") -> DeniedClaim:
    return DeniedClaim(
        split=split,
        features=features(DenialReasonCategory.OTHER, amount),
        appeal_success_proxy=proxy,
        rule_chance=Decimal(chance),
    )


def _data(*claims: DeniedClaim) -> TrainingData:
    return TrainingData(split_seed=SPLIT_SEED, claims=claims)


def _made_up_data() -> TrainingData:
    """The made-up training rows: six in ten in train, two in validation, two in test."""
    claims = []
    for index, row in enumerate(training_rows()):
        place = index % 10
        split = TRAIN if place < 6 else VALIDATION if place < 8 else TEST
        claims.append(
            DeniedClaim(
                split=split,
                features=row.features,
                appeal_success_proxy=row.appeal_success_proxy,
                rule_chance=Decimal("0.90" if row.appeal_success_proxy else "0.10"),
            )
        )
    return TrainingData(split_seed=SPLIT_SEED, claims=tuple(claims))


def test_auc_is_the_share_of_true_and_false_pairs_the_model_orders_right() -> None:
    data = _data(
        _claim(TEST, "300.00", True),
        _claim(TEST, "100.00", True),
        _claim(TEST, "200.00", False),
        _claim(TEST, "100.00", False),
    )

    score = score_saved_model(AMOUNT_MODEL, data, TEST)

    # Four pairs: 300 beats 200 and 100, 100 loses to 200, 100 ties 100 (half).
    assert score.auc == 0.625
    assert (score.split, score.rows, score.proxy_true) == (TEST, 4, 2)


def test_only_the_claims_of_the_asked_split_are_scored() -> None:
    data = _data(
        _claim(TEST, "300.00", True),
        _claim(TEST, "100.00", False),
        # The other splits order every pair wrongly.
        _claim(TRAIN, "100.00", True),
        _claim(TRAIN, "300.00", False),
        _claim(VALIDATION, "100.00", True),
        _claim(VALIDATION, "300.00", False),
        _claim(VALIDATION, "400.00", False),
    )

    test, validation = (
        score_saved_model(AMOUNT_MODEL, data, split) for split in (TEST, VALIDATION)
    )

    assert (test.rows, test.proxy_true, test.auc) == (2, 1, 1.0)
    assert (validation.rows, validation.proxy_true, validation.auc) == (3, 1, 0.0)


def test_best_possible_auc_comes_from_the_rules_chance_and_not_the_model() -> None:
    data = _data(
        # The model orders this pair wrongly, the rule's chance orders it right.
        _claim(TEST, "100.00", True, chance="0.90"),
        _claim(TEST, "300.00", False, chance="0.10"),
    )

    score = score_saved_model(AMOUNT_MODEL, data, TEST)

    assert score.auc == 0.0
    assert score.best_possible_auc == 1.0


@pytest.mark.parametrize("proxy", [True, False])
def test_a_split_with_one_kind_of_proxy_has_no_auc(proxy: bool) -> None:
    data = _data(_claim(TEST, "100.00", proxy), _claim(TEST, "300.00", proxy))

    score = score_saved_model(AMOUNT_MODEL, data, TEST)

    assert (score.rows, score.proxy_true) == (2, 2 if proxy else 0)
    assert score.auc is None
    assert score.best_possible_auc is None


def test_a_split_with_no_claims_has_no_auc() -> None:
    data = _data(_claim(TRAIN, "100.00", True), _claim(TRAIN, "50.00", False))

    score = score_saved_model(AMOUNT_MODEL, data, TEST)

    assert (score.rows, score.proxy_true, score.auc, score.best_possible_auc) == (0, 0, None, None)


def test_a_trained_model_scores_the_same_as_in_its_training_report() -> None:
    data = _made_up_data()
    model, report = train_and_score(data, SEED)

    for split in DatasetSplit:
        score = score_saved_model(model, data, split)
        trained = report.splits[split]
        assert score.auc is not None
        assert (score.rows, score.proxy_true) == (trained.rows, trained.proxy_true)
        assert (score.auc, score.best_possible_auc) == (trained.auc, trained.best_possible_auc)


def test_scoring_does_not_change_the_model() -> None:
    data = _made_up_data()
    model, _ = train_and_score(data, SEED)

    first = score_saved_model(model, data, TEST)
    second = score_saved_model(model, data, TEST)

    assert first == second


def test_a_score_cannot_be_changed_or_hold_an_auc_outside_0_to_1() -> None:
    score = score_saved_model(AMOUNT_MODEL, _data(_claim(TEST, "100.00", True)), TEST)

    with pytest.raises(ValidationError):
        score.rows = 5
    with pytest.raises(ValidationError):
        ModelScore(split=TEST, rows=2, proxy_true=1, auc=1.2, best_possible_auc=0.75)
