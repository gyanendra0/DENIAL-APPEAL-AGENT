"""Load the CMS national coverage determinations into `evidence_documents` and `evidence_chunks`.

Usage:
    python3 -m pipelines.load_evidence_corpus <path-to-ncd.zip>

The file is the "Current NCD Data" download of the CMS Medicare Coverage Database, fetched by
hand. Each determination becomes one document and its policy text is cut into chunks. The
stored determinations are replaced, all in one transaction: afterwards the tables hold only
the determinations of this file. Retired notices are left out. No model is called.

Exit codes: 0 loaded, 1 file rejected by a quality gate (nothing written), 2 bad arguments.
"""

import argparse
import logging
import statistics
import sys
from collections.abc import Sequence
from pathlib import Path

from src.config.settings import get_settings
from src.db.session import create_db_engine, create_session_factory, session_scope
from src.ingest.evidence_corpus import read_evidence_corpus, replace_evidence_corpus
from src.ingest.marketplace_denials import BatchRejectedError
from src.rag.chunking import CHUNKER_VERSION

EXIT_OK = 0
EXIT_REJECTED = 1


def main(argv: Sequence[str] | None = None) -> int:
    """Read, validate, chunk and store the determinations of one file in a single transaction."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("path", type=Path, help="the downloaded ncd.zip")
    args = parser.parse_args(argv)
    if not args.path.is_file():
        parser.error(f"file not found: {args.path}")

    logging.basicConfig(level=get_settings().log_level)
    try:
        batch = read_evidence_corpus(args.path)
    except BatchRejectedError as exc:
        print(exc, file=sys.stderr)
        return EXIT_REJECTED

    engine = create_db_engine()
    try:
        with session_scope(create_session_factory(engine)) as session:
            documents, chunks = replace_evidence_corpus(session, batch)
    finally:
        engine.dispose()
    lengths = [len(chunk.text) for entry in batch.entries for chunk in entry.chunks]
    print(f"loaded {documents} documents and {chunks} chunks (chunker {CHUNKER_VERSION})")
    print(f"  retired notices left out: {batch.retired_skipped}")
    print(f"  file date: {batch.entries[0].document.source_file_date.isoformat()}")
    print(f"  chunk length: longest {max(lengths)}, median {statistics.median(lengths):g}")
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
