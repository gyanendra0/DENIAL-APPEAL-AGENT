"""Measure the Stage 3 numbers on what is stored and check them against their targets.

Usage:
    python3 -m pipelines.run_evaluation --rules-today 2010-01-01 [--split test]
        [--prompt-version v2]

Run `pipelines.extract_documents` and `pipelines.train_win_model` first: this command reads
the documents and their answer keys, the stored extraction results, the labels and lines, and
the saved model in the folder named by the `MODEL_DIR` setting. It only reads: it makes no
model call, spends no money, trains nothing and writes nothing.

It prints the field extraction accuracy (exact match with the answer keys; a document with no
current result counts as all wrong), the denial letters' headline reason, the saved model's
AUC beside the best possible AUC, and how often the rules give an extracted letter the
verdict its answer key gets. Three numbers are checked against the Stage 3 done condition:
field accuracy and the headline reason against 85%, the AUC against 0.70.

`--rules-today` is the date the rules take as "today". It has no default, because the rules
never read the clock; the made-up deadlines run from 2008 to 2011. The amount floor comes
from the `RULES_AMOUNT_FLOOR_USD` setting.

All data is generated: made-up documents of the synthetic claims sample, and a label that is
a proxy. The numbers say nothing about real appeals.

Exit codes: 0 all three numbers meet their targets, 1 one is below its target or cannot be
measured (no saved model, stored labels that cannot be used, no extraction result) or the
split has no document, 2 bad arguments.
"""

import argparse
import logging
import sys
from collections.abc import Sequence
from datetime import date

from src.config.settings import get_settings
from src.db.models import DatasetSplit
from src.db.session import create_db_engine, create_session_factory, session_scope
from src.evaluation.model_score import ModelScore, score_saved_model
from src.evaluation.report import build_evaluation_report
from src.extraction.prompts import EXTRACTION_PROMPT_VERSION
from src.extraction.store import read_extraction_rows, select_documents
from src.ingest.marketplace_denials import BatchRejectedError
from src.ml.inference import ModelFileError, load_win_model
from src.ml.win_model_run import TrainingData, load_training_data

EXIT_OK = 0
EXIT_NOT_MET = 1


def _day(text: str) -> date:
    try:
        return date.fromisoformat(text)
    except ValueError:
        raise argparse.ArgumentTypeError("must be a date like 2010-01-01") from None


def _first_problem(error: BatchRejectedError) -> str:
    more = len(error.problems) - 1
    return error.problems[0] + (f" (and {more} more)" if more else "")


def main(argv: Sequence[str] | None = None) -> int:
    """Print the evaluation report of one split and say whether the targets are met.

    With the default load (1,883 test documents, 5,325 denied claims) this takes a few
    seconds, most of it reading the claims.
    """
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--split",
        type=DatasetSplit,
        choices=list(DatasetSplit),
        default=DatasetSplit.TEST,
        help=f"the dataset split that is measured (default {DatasetSplit.TEST.value})",
    )
    parser.add_argument(
        "--prompt-version",
        default=EXTRACTION_PROMPT_VERSION,
        help="the prompt version whose extraction results are measured"
        f" (default {EXTRACTION_PROMPT_VERSION})",
    )
    parser.add_argument(
        "--rules-today",
        type=_day,
        required=True,
        help="the date the rules take as today, as YYYY-MM-DD (no default)",
    )
    args = parser.parse_args(argv)

    settings = get_settings()
    logging.basicConfig(level=settings.log_level)
    data: TrainingData | None = None
    model: ModelScore | None = None
    model_problem: str | None = None
    engine = create_db_engine()
    try:
        with session_scope(create_session_factory(engine)) as session:
            documents = select_documents(session, args.split)
            rows = read_extraction_rows(session, args.prompt_version)
            if documents:
                try:
                    data = load_training_data(session)
                except BatchRejectedError as exc:
                    model_problem = _first_problem(exc)
    finally:
        engine.dispose()
    if not documents:
        print(
            f"the {args.split.value} split has no document: run pipelines.generate_documents"
            " first",
            file=sys.stderr,
        )
        return EXIT_NOT_MET

    if data is not None:
        try:
            model = score_saved_model(load_win_model(settings.model_dir), data, args.split)
        except ModelFileError as exc:
            model_problem = f"{exc}: run pipelines.train_win_model first"

    report = build_evaluation_report(
        documents,
        rows,
        split=args.split,
        prompt_version=args.prompt_version,
        rules_today=args.rules_today,
        amount_floor=settings.rules_amount_floor_usd,
        model=model,
        model_problem=model_problem,
    )
    print(report.as_text())
    return EXIT_OK if report.targets_met else EXIT_NOT_MET


if __name__ == "__main__":
    raise SystemExit(main())
