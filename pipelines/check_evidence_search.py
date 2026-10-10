"""Check how often the evidence search finds the document a hand-made question is about.

Usage:
    python3 -m pipelines.check_evidence_search <path-to-questions.json>

The question file is a JSON list; each entry has a `question` and the
`expected_section_number` of the one determination a good search brings back. The project's
own set is `config/evaluation/evidence_search_questions.json`. Run
`pipelines.embed_evidence_corpus` first: the search reads the vectors it stored.

Each question is embedded through the LLM gateway and searched. THIS SPENDS MONEY: one small
embedding call per question (20 questions cost far below one cent). The gateway needs its
`LLM_` settings keys. Nothing is written except the gateway's own record of each call.

The command prints the share of questions whose expected determination is among the top
results, and every miss with what came back instead. The share is recorded, it is not a
gate: a low share still exits 0.

Exit codes: 0 the share was measured, 1 it could not be measured (no vector is stored, the
monthly budget is reached, the provider cannot answer or refuses), 2 bad arguments or a
question file that cannot be used.
"""

import argparse
import logging
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from src.config.settings import get_settings, load_llm_gateway_settings
from src.db.models import EvidenceSource
from src.db.session import create_db_engine, create_session_factory, session_scope
from src.evaluation.search_quality import (
    SearchQualityReport,
    SearchQuestionFileError,
    read_search_questions,
    run_search_quality_check,
    unknown_section_numbers,
)
from src.llm.budget import month_to_date_spend
from src.llm.gateway import (
    EmbeddingAnswerError,
    LlmBudgetExceededError,
    LlmUnavailableError,
    ProviderRejectedError,
)
from src.llm.openai_provider import build_openai_gateway
from src.rag.embedding_input import EMBEDDING_INPUT_VERSION
from src.rag.retrieval import (
    DEFAULT_TOP_K,
    EvidenceNotEmbeddedError,
    QueryEmbeddingError,
    search_evidence,
)

EXIT_OK = 0
EXIT_NOT_MEASURED = 1
EXIT_BAD_INPUT = 2
# The questions name determinations of this source.
QUESTION_SOURCE = EvidenceSource.CMS_NCD


def main(argv: Sequence[str] | None = None) -> int:
    """Search for every question of one file and print the share that found its document.

    Spends money: one embedding call per question, through the gateway and its budget check.
    The project's 20 questions took 21 seconds and cost $0.000020 (measured 2026-10-11).
    """
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("path", type=Path, help="the question file (JSON)")
    args = parser.parse_args(argv)
    if not args.path.is_file():
        parser.error(f"file not found: {args.path}")
    try:
        questions = read_search_questions(args.path)
    except SearchQuestionFileError as exc:
        print(exc, file=sys.stderr)
        return EXIT_BAD_INPUT

    settings = get_settings()
    gateway_settings = load_llm_gateway_settings()
    model_name = gateway_settings.llm_embedding_model

    logging.basicConfig(level=settings.log_level)
    report: SearchQualityReport | None = None
    problem: str | None = None
    engine = create_db_engine()
    try:
        session_factory = create_session_factory(engine)
        gateway = build_openai_gateway(gateway_settings, session_factory)
        with session_scope(session_factory) as session:
            unknown = unknown_section_numbers(session, questions, QUESTION_SOURCE)
            if unknown:
                print(
                    "the question file expects section numbers no stored determination has: "
                    + ", ".join(unknown),
                    file=sys.stderr,
                )
                return EXIT_BAD_INPUT
            spent_before = month_to_date_spend(session, datetime.now(UTC))

            def search(question: str) -> list[str]:
                matches = search_evidence(
                    session,
                    gateway,
                    question,
                    model_name=model_name,
                    top_k=DEFAULT_TOP_K,
                    source=QUESTION_SOURCE,
                )
                return [match.section_number for match in matches]

            try:
                report = run_search_quality_check(questions, search)
            except EvidenceNotEmbeddedError as exc:
                problem = str(exc)
            except LlmBudgetExceededError:
                problem = "the monthly LLM budget is reached"
            except LlmUnavailableError:
                problem = "the embedding provider could not answer"
            except ProviderRejectedError as exc:
                problem = f"the provider refused the request: {exc}"
            except (EmbeddingAnswerError, QueryEmbeddingError) as exc:
                problem = f"an answer cannot be used (the call was paid for): {exc}"

        with session_scope(session_factory) as session:
            spent_after = month_to_date_spend(session, datetime.now(UTC))
    finally:
        engine.dispose()

    if report is not None:
        print(
            f"top-{DEFAULT_TOP_K} share: {report.hits} of {report.questions} questions"
            f" ({report.hit_share:.2%}) found their determination"
            f" (model {model_name}, input version {EMBEDDING_INPUT_VERSION})"
        )
        for miss in report.misses:
            found = ", ".join(miss.found_section_numbers) or "nothing"
            print(
                f"  missed question {miss.question_number}:"
                f" expected {miss.expected_section_number}, found {found}"
            )
    print(
        f"  spent ${spent_after - spent_before} in this run; ${spent_after} of the"
        f" ${gateway_settings.llm_monthly_budget_usd} monthly budget is used"
    )
    if problem is not None:
        print(f"the share cannot be measured: {problem}", file=sys.stderr)
        return EXIT_NOT_MEASURED
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
