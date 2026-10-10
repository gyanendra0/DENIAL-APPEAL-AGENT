"""Embed the stored evidence chunks into `evidence_chunk_embeddings`.

Usage:
    python3 -m pipelines.embed_evidence_corpus

Run `pipelines.load_evidence_corpus` first: this command reads the chunks it stored. The
chunks are sent to the embedding model through the LLM gateway, a batch at a time, and each
batch of vectors is saved as soon as it arrives. THIS SPENDS MONEY: one embedding call per
batch of chunks that have no vector yet. The gateway needs its `LLM_` settings keys.

A chunk that already has a vector for the configured model and the input version is skipped,
so a second run embeds nothing and a run that stopped can be started again. When the monthly
budget is reached, the provider cannot answer or refuses the request (a bad request, a wrong
key), or an answer cannot be stored (a vector of the wrong size), the run stops and keeps the
batches it finished. It also stops when the chunks change during the run: a load of the corpus
at the same time removes them, and the vectors stored so far with them. Only one run works at
a time: a second one started meanwhile stops before it calls the provider and spends nothing.

At the end the command prints the counts, the time and the money spent.

Exit codes: 0 every chunk has a vector, 1 the run stopped early or no chunk is stored, 2 bad
arguments.
"""

import argparse
import logging
import sys
import time
from collections.abc import Sequence
from datetime import UTC, datetime

from src.config.settings import get_settings, load_llm_gateway_settings
from src.db.session import create_db_engine, create_session_factory, session_scope
from src.llm.budget import month_to_date_spend
from src.llm.openai_provider import build_openai_gateway
from src.rag.embedding_input import EMBEDDING_INPUT_VERSION
from src.rag.embedding_run import run_evidence_embedding

EXIT_OK = 0
EXIT_STOPPED = 1


def main(argv: Sequence[str] | None = None) -> int:
    """Embed the stored chunks that have no vector yet and store the vectors.

    Spends money: one embedding call per batch of chunks without a vector. The 1,120 chunks
    of the first load took 37 seconds and cost $0.003884 (measured 2026-10-11).
    """
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.parse_args(argv)

    settings = get_settings()
    gateway_settings = load_llm_gateway_settings()
    model_name = gateway_settings.llm_embedding_model

    logging.basicConfig(level=settings.log_level)
    engine = create_db_engine()
    try:
        session_factory = create_session_factory(engine)
        with session_scope(session_factory) as session:
            spent_before = month_to_date_spend(session, datetime.now(UTC))

        gateway = build_openai_gateway(gateway_settings, session_factory)
        started = time.monotonic()
        summary = run_evidence_embedding(session_factory, gateway, model_name=model_name)
        seconds = time.monotonic() - started

        with session_scope(session_factory) as session:
            spent_after = month_to_date_spend(session, datetime.now(UTC))
    finally:
        engine.dispose()

    if summary.chunks == 0:
        print(
            "no evidence chunk is stored: run pipelines.load_evidence_corpus first",
            file=sys.stderr,
        )
        return EXIT_STOPPED
    print(
        f"embedded {summary.embedded} of {summary.chunks} chunks"
        f" (model {model_name}, input version {EMBEDDING_INPUT_VERSION})"
    )
    print(f"  already embedded before this run: {summary.already_embedded}")
    print(f"  time: {seconds:.0f} s")
    print(
        f"  spent ${spent_after - spent_before} in this run; ${spent_after} of the"
        f" ${gateway_settings.llm_monthly_budget_usd} monthly budget is used"
    )
    if summary.stop_reason is not None:
        print(
            f"stopped early: {summary.stop_reason}; {summary.not_tried} chunks have no vector."
            " Run the same command again to continue.",
            file=sys.stderr,
        )
        return EXIT_STOPPED
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
