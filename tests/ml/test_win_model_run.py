from decimal import Decimal

import pytest
from sqlalchemy import delete, update
from sqlalchemy.orm import Session

from src.db.models import ClaimSampleLabel, ClaimSampleLine, DatasetSplit, DenialReasonCategory
from src.ingest.marketplace_denials import BatchRejectedError
from src.ml.auc import pairwise_auc
from src.ml.features import ClaimFeatures
from src.ml.inference import predict_win_probabilities
from src.ml.training import SplitFacts
from src.ml.win_model_run import (
    DeniedClaim,
    SplitScore,
    TrainingData,
    WinModelReport,
    load_training_data,
    train_and_score,
)
from tests.ml.made_up_claims import (
    DENIED_COUNT,
    DUPLICATE_CLAIM,
    NECESSITY_CLAIM,
    SPLIT_SEED,
    store_made_up_claims,
)
from tests.ml.made_up_rows import AMOUNTS, features, training_rows

TRAIN = DatasetSplit.TRAIN
VALIDATION = DatasetSplit.VALIDATION
TEST = DatasetSplit.TEST
SEED = 7


def _split(index: int) -> DatasetSplit:
    """Six rows in ten go to train, two to validation, two to test."""
    place = index % 10
    if place < 6:
        return TRAIN
    return VALIDATION if place < 8 else TEST


def _data(
    *, flip_outside_train: bool = False, chance_true: str = "0.90", chance_false: str = "0.10"
) -> TrainingData:
    """The made-up training rows as a run's data; the rule's chance is invented too."""
    claims = []
    for index, row in enumerate(training_rows()):
        split = _split(index)
        proxy = row.appeal_success_proxy
        if flip_outside_train and split is not TRAIN:
            proxy = not proxy
        claims.append(
            DeniedClaim(
                split=split,
                features=row.features,
                appeal_success_proxy=proxy,
                rule_chance=Decimal(chance_true if proxy else chance_false),
            )
        )
    return TrainingData(split_seed=SPLIT_SEED, claims=tuple(claims))


def _every_feature_row() -> list[ClaimFeatures]:
    return [
        features(category, amount, fully_denied=fully_denied)
        for category in DenialReasonCategory
        for amount in AMOUNTS
        for fully_denied in (False, True)
    ]


def _score(auc: float | None, rows: int = 10, proxy_true: int = 4) -> SplitScore:
    return SplitScore(rows=rows, proxy_true=proxy_true, auc=auc, best_possible_auc=0.75)


def _report(test_auc: float | None) -> WinModelReport:
    return WinModelReport(
        split_seed=42,
        training_seed=7,
        splits={
            TRAIN: _score(0.775, rows=3784, proxy_true=1976),
            VALIDATION: _score(None, rows=0, proxy_true=0),
            TEST: _score(test_auc),
        },
    )


def test_report_counts_the_rows_and_true_proxies_of_every_split() -> None:
    data = _data()

    _, report = train_and_score(data, SEED)

    for split in DatasetSplit:
        in_split = [claim for claim in data.claims if claim.split is split]
        assert report.splits[split].rows == len(in_split)
        assert report.splits[split].proxy_true == sum(c.appeal_success_proxy for c in in_split)
    assert sum(score.rows for score in report.splits.values()) == len(data.claims) == 288
    assert report.split_seed == SPLIT_SEED
    assert report.training_seed == SEED


def test_the_model_learns_the_made_up_rule() -> None:
    _, report = train_and_score(_data(), SEED)

    for split in DatasetSplit:
        auc = report.splits[split].auc
        assert auc is not None
        assert auc > 0.9


def test_the_model_auc_is_the_shared_auc_function_on_the_models_own_scores() -> None:
    data = _data()

    model, report = train_and_score(data, SEED)

    test_claims = [claim for claim in data.claims if claim.split is TEST]
    scores = predict_win_probabilities(model, [claim.features for claim in test_claims])
    expected = pairwise_auc(
        [s for claim, s in zip(test_claims, scores, strict=True) if claim.appeal_success_proxy],
        [s for claim, s in zip(test_claims, scores, strict=True) if not claim.appeal_success_proxy],
    )
    assert report.splits[TEST].auc == expected


def test_only_the_train_split_reaches_the_fit() -> None:
    # Flipping every proxy outside train would change a model that had seen those rows.
    model, report = train_and_score(_data(), SEED)
    other_model, other_report = train_and_score(_data(flip_outside_train=True), SEED)

    rows = _every_feature_row()
    assert predict_win_probabilities(model, rows) == predict_win_probabilities(other_model, rows)
    assert report.splits[TRAIN] == other_report.splits[TRAIN]
    assert report.splits[TEST].auc != other_report.splits[TEST].auc


def test_the_same_seed_gives_the_same_predictions() -> None:
    model, report = train_and_score(_data(), SEED)
    again, report_again = train_and_score(_data(), SEED)

    rows = _every_feature_row()
    assert predict_win_probabilities(model, rows) == predict_win_probabilities(again, rows)
    assert report == report_again


def test_best_possible_auc_scores_the_rules_chance_not_the_model() -> None:
    _, report = train_and_score(_data(chance_true="0.90", chance_false="0.10"), SEED)
    _, level = train_and_score(_data(chance_true="0.50", chance_false="0.50"), SEED)

    for split in DatasetSplit:
        assert report.splits[split].best_possible_auc == 1.0
        assert level.splits[split].best_possible_auc == 0.5
        # The rule's chance is not a model input: the model scores the same either way.
        assert report.splits[split].auc == level.splits[split].auc


@pytest.mark.parametrize("proxy", [True, False])
def test_a_train_split_with_one_proxy_value_is_refused(proxy: bool) -> None:
    data = _data()
    kept = tuple(
        claim
        for claim in data.claims
        if claim.split is not TRAIN or claim.appeal_success_proxy is proxy
    )

    with pytest.raises(BatchRejectedError, match="do not include both a true and a false proxy"):
        train_and_score(TrainingData(split_seed=SPLIT_SEED, claims=kept), SEED)


def test_an_empty_train_split_is_refused() -> None:
    kept = tuple(claim for claim in _data().claims if claim.split is not TRAIN)

    with pytest.raises(BatchRejectedError, match="the train split has 0 denied claims"):
        train_and_score(TrainingData(split_seed=SPLIT_SEED, claims=kept), SEED)


def test_a_split_without_claims_has_no_auc() -> None:
    kept = tuple(claim for claim in _data().claims if claim.split is not VALIDATION)

    _, report = train_and_score(TrainingData(split_seed=SPLIT_SEED, claims=kept), SEED)

    assert report.splits[VALIDATION] == SplitScore(
        rows=0, proxy_true=0, auc=None, best_possible_auc=None
    )


def test_a_denied_claim_carries_no_claim_id_split_seed_or_document_field() -> None:
    assert set(DeniedClaim.model_fields) == {
        "split",
        "features",
        "appeal_success_proxy",
        "rule_chance",
    }


def test_split_facts_are_the_counts_and_the_model_auc() -> None:
    facts = _report(0.7539).split_facts()

    assert facts[TRAIN] == SplitFacts(rows=3784, proxy_true=1976, auc=0.775)
    assert facts[VALIDATION] == SplitFacts(rows=0, proxy_true=0, auc=None)
    assert facts[TEST] == SplitFacts(rows=10, proxy_true=4, auc=0.7539)


def test_text_gives_every_split_beside_the_best_possible_auc_and_the_target() -> None:
    assert _report(0.7539).as_text().splitlines() == [
        "win-probability model v1 (features v1, label rule v2; the appeal-success label is a"
        " proxy, not an observed outcome)",
        "training seed: 7 | split seed: 42",
        "split: denied claims | proxy true (of denied) | model AUC | best possible AUC",
        "train: 3,784 | 1,976 (52.22%) | 0.7750 | 0.7500",
        "validation: 0 | 0 (n/a) | n/a | 0.7500",
        "test: 10 | 4 (40.00%) | 0.7539 | 0.7500",
        "test AUC against the target 0.70 (reported, not a gate): 0.7539, meets the target",
    ]


@pytest.mark.parametrize(
    ("test_auc", "ending"),
    [
        (0.70, "0.7000, meets the target"),
        (0.6999, "0.6999, below the target"),
        (None, "no AUC, the test split needs a true and a false proxy"),
    ],
)
def test_text_says_whether_the_test_auc_meets_the_target(
    test_auc: float | None, ending: str
) -> None:
    assert _report(test_auc).as_text().endswith(f"(reported, not a gate): {ending}")


def test_reads_every_denied_claim_and_no_paid_one(session: Session) -> None:
    store_made_up_claims(session)

    data = load_training_data(session)

    assert len(data.claims) == DENIED_COUNT
    assert data.split_seed == SPLIT_SEED
    assert {claim.split for claim in data.claims} == set(DatasetSplit)
    # Claim id order: the first claim is the medical-necessity one, the second the duplicate.
    assert data.claims[0].features == features(
        DenialReasonCategory.MEDICAL_NECESSITY, "300.00", fully_denied=False
    )
    assert data.claims[0].rule_chance == Decimal("0.98")
    assert data.claims[1].features == features(
        DenialReasonCategory.DUPLICATE, "0.00", fully_denied=True
    )
    assert data.claims[1].rule_chance == Decimal("0.02")


def test_the_stored_proxy_and_split_are_the_ones_read(session: Session) -> None:
    store_made_up_claims(session)
    stored = {
        label.source_claim_id: label
        for label in session.query(ClaimSampleLabel).filter(ClaimSampleLabel.is_denied)
    }

    data = load_training_data(session)

    for claim, claim_id in zip(data.claims, sorted(stored), strict=True):
        assert claim.appeal_success_proxy is stored[claim_id].appeal_success_proxy
        assert claim.split is stored[claim_id].split


def test_stored_claims_train_a_model_that_learns_their_rule(session: Session) -> None:
    store_made_up_claims(session)

    _, report = train_and_score(load_training_data(session), SEED)

    for split in DatasetSplit:
        auc = report.splits[split].auc
        assert auc is not None
        assert auc > 0.9


def test_no_denied_claim_is_refused(session: Session) -> None:
    with pytest.raises(BatchRejectedError, match="no claim is labelled denied"):
        load_training_data(session)


def test_labels_of_another_rule_version_are_refused(session: Session) -> None:
    store_made_up_claims(session, count=6)
    session.execute(update(ClaimSampleLabel).values(label_rule_version="v0"))

    with pytest.raises(BatchRejectedError, match="rule version v0, the code is at v2"):
        load_training_data(session)


def test_labels_split_with_two_seeds_are_refused(session: Session) -> None:
    store_made_up_claims(session, count=6)
    session.execute(
        update(ClaimSampleLabel)
        .where(ClaimSampleLabel.source_claim_id == DUPLICATE_CLAIM)
        .values(split_seed=7)
    )

    with pytest.raises(BatchRejectedError, match=r"more than one seed \(7, 42\)"):
        load_training_data(session)


def test_a_label_that_no_longer_fits_its_lines_is_refused(session: Session) -> None:
    store_made_up_claims(session, count=6)
    # The claim's denied line becomes a noncovered one: the stored category is now wrong.
    session.execute(
        update(ClaimSampleLine)
        .where(ClaimSampleLine.source_claim_id == NECESSITY_CLAIM)
        .where(ClaimSampleLine.line_number == 1)
        .values(processing_indicator="C")
    )

    with pytest.raises(BatchRejectedError, match=f"claim {NECESSITY_CLAIM}: the stored label"):
        load_training_data(session)


def test_a_denied_label_whose_lines_are_now_paid_is_refused(session: Session) -> None:
    store_made_up_claims(session, count=6)
    session.execute(
        update(ClaimSampleLine)
        .where(ClaimSampleLine.source_claim_id == DUPLICATE_CLAIM)
        .values(processing_indicator="A")
    )

    with pytest.raises(BatchRejectedError, match=f"claim {DUPLICATE_CLAIM}: the stored label"):
        load_training_data(session)


def test_a_denied_label_without_stored_lines_is_refused(session: Session) -> None:
    store_made_up_claims(session, count=6)
    session.execute(
        delete(ClaimSampleLine).where(ClaimSampleLine.source_claim_id == DUPLICATE_CLAIM)
    )

    with pytest.raises(BatchRejectedError, match=f"claim {DUPLICATE_CLAIM}: the stored label"):
        load_training_data(session)
