"""Train the win-probability model on the stored denied claims and save it.

Usage:
    python3 -m pipelines.train_win_model [--seed 42]

Run `pipelines.run_data_pipeline` first: this command reads the lines and labels it stored.
The model is fitted on the train split only. The command prints, per split, the model's AUC
beside the best possible AUC (the score of the label rule's own chance) and the 0.70 target,
then writes two files into the folder named by the `MODEL_DIR` setting: the model file and a
JSON file of facts (versions, seeds, row counts, AUC per split, the model file's SHA-256).
Files of an earlier run are replaced. The same claims, labels and seed give the same model.

The label is a proxy made by the label rule, so a high AUC means the model learned that rule.
It says nothing about real appeals.

The AUC is reported and is not a gate: a test AUC below the target still saves the model and
exits 0, because a small load gives a noisy AUC.

Exit codes: 0 trained and saved, 1 nothing written (the stored labels cannot be used: none
denied, made by another label rule version, split with more than one seed, or a label that no
longer fits the claim's stored lines; or the train split lacks a true or a false proxy),
2 bad arguments.
"""

import argparse
import logging
import sys
from collections.abc import Sequence

from src.config.settings import get_settings
from src.db.session import create_db_engine, create_session_factory, session_scope
from src.ingest.marketplace_denials import BatchRejectedError
from src.ml.training import FACTS_FILE_NAME, MODEL_FILE_NAME, save_win_model
from src.ml.win_model_run import MAX_TRAINING_SEED, load_training_data, train_and_score

EXIT_OK = 0
EXIT_REJECTED = 1
DEFAULT_SEED = 42


def _seed(text: str) -> int:
    value = int(text)
    if not 0 <= value <= MAX_TRAINING_SEED:
        raise argparse.ArgumentTypeError(f"must be between 0 and {MAX_TRAINING_SEED}")
    return value


def main(argv: Sequence[str] | None = None) -> int:
    """Train the model, print its AUC per split and save it.

    With the default load of 50,000 claims this takes a few seconds, most of it reading the
    claims.
    """
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--seed",
        type=_seed,
        default=DEFAULT_SEED,
        help=f"seed of the model's fit (default {DEFAULT_SEED})",
    )
    args = parser.parse_args(argv)

    settings = get_settings()
    logging.basicConfig(level=settings.log_level)
    engine = create_db_engine()
    try:
        with session_scope(create_session_factory(engine)) as session:
            data = load_training_data(session)
        model, report = train_and_score(data, args.seed)
    except BatchRejectedError as exc:
        print(exc, file=sys.stderr)
        return EXIT_REJECTED
    finally:
        engine.dispose()
    print(report.as_text())
    save_win_model(
        model, settings.model_dir, split_seed=report.split_seed, splits=report.split_facts()
    )
    print(f"saved {MODEL_FILE_NAME} and {FACTS_FILE_NAME} in {settings.model_dir}")
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
