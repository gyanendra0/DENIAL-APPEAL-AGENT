"""The smoke test's made-up model is one the API's own loader accepts."""

from pathlib import Path

from pipelines.make_smoke_model import EXIT_OK, main, smoke_rows
from src.ml.inference import load_win_model, predict_win_probability
from src.ml.training import FACTS_FILE_NAME, MODEL_FILE_NAME


def test_the_files_written_pass_the_loaders_checks_and_can_score(tmp_path: Path) -> None:
    assert main(["--out", str(tmp_path)]) == EXIT_OK

    model = load_win_model(tmp_path)

    assert 0 <= predict_win_probability(model, smoke_rows()[0].features) <= 1


def test_only_the_two_model_files_are_written(tmp_path: Path) -> None:
    main(["--out", str(tmp_path)])

    assert {path.name for path in tmp_path.iterdir()} == {MODEL_FILE_NAME, FACTS_FILE_NAME}


def test_the_files_fit_in_a_configmap(tmp_path: Path) -> None:
    main(["--out", str(tmp_path)])

    assert sum(path.stat().st_size for path in tmp_path.iterdir()) < 1024 * 1024


def test_the_rows_hold_both_proxy_values() -> None:
    assert {row.appeal_success_proxy for row in smoke_rows()} == {True, False}
