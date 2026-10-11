"""Load CMS manual chapters (PDF files) into `evidence_documents` and `evidence_chunks`.

Usage:
    python3 -m pipelines.load_manual_chapters <chapter.pdf> [<chapter.pdf> ...] --file-date DATE

The files are chapters of the CMS Internet-Only Manuals, fetched by hand. Each numbered section
becomes one document and its text is cut into chunks. `--file-date` (YYYY-MM-DD) is the day the
files were downloaded: the files state no release date. The stored manual sections are replaced,
all in one transaction: afterwards the tables hold only the sections of these files, so a
chapter that is not named is removed. The coverage determinations stay as they are. The new
chunks have no vector until `pipelines.embed_evidence_corpus` runs. No model is called.

Exit codes: 0 loaded, 1 a file rejected by a quality gate (nothing written), 2 bad arguments.
"""

import argparse
import logging
import statistics
import sys
from collections import Counter
from collections.abc import Sequence
from datetime import date, datetime
from pathlib import Path

from src.config.settings import get_settings
from src.db.session import create_db_engine, create_session_factory, session_scope
from src.ingest.manual_chapters import (
    ManualChapterBatch,
    read_manual_chapters,
    replace_manual_chapters,
)
from src.ingest.marketplace_denials import BatchRejectedError
from src.rag.chunking import CHUNKER_VERSION

EXIT_OK = 0
EXIT_REJECTED = 1
FILE_DATE_FORMAT = "%Y-%m-%d"


def main(argv: Sequence[str] | None = None) -> int:
    """Read, validate, chunk and store the sections of the files in a single transaction.

    Takes a few seconds per real chapter.
    """
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("paths", type=Path, nargs="+", help="the downloaded chapter PDFs")
    parser.add_argument(
        "--file-date",
        type=_file_date,
        required=True,
        help="the day the files were downloaded, as YYYY-MM-DD",
    )
    args = parser.parse_args(argv)
    for path in args.paths:
        if not path.is_file():
            parser.error(f"file not found: {path}")
    if args.file_date > date.today():
        parser.error(f"--file-date is in the future: {args.file_date.isoformat()}")

    logging.basicConfig(level=get_settings().log_level)
    try:
        batch = read_manual_chapters(args.paths, source_file_date=args.file_date)
    except BatchRejectedError as exc:
        print(exc, file=sys.stderr)
        return EXIT_REJECTED

    engine = create_db_engine()
    try:
        with session_scope(create_session_factory(engine)) as session:
            documents, chunks = replace_manual_chapters(session, batch)
    finally:
        engine.dispose()
    print(f"loaded {documents} documents and {chunks} chunks (chunker {CHUNKER_VERSION})")
    for line in _report_lines(batch, args.file_date):
        print(f"  {line}")
    return EXIT_OK


def _file_date(value: str) -> date:
    try:
        return datetime.strptime(value, FILE_DATE_FORMAT).date()
    except ValueError:
        raise argparse.ArgumentTypeError(f"not a date written as YYYY-MM-DD: {value!r}") from None


def _report_lines(batch: ManualChapterBatch, file_date: date) -> list[str]:
    """The counts of one load. Never a title or any text of a file."""
    documents: Counter[str] = Counter()
    chunks: Counter[str] = Counter()
    for entry in batch.entries:
        # `100-04-30-150.5.2.1` is section 150.5.2.1 of chapter `100-04-30`.
        chapter = entry.document.section_number.rsplit("-", 1)[0]
        documents[chapter] += 1
        chunks[chapter] += len(entry.chunks)
    lengths = [len(chunk.text) for entry in batch.entries for chunk in entry.chunks]
    return [
        *(
            f"chapter {chapter}: {count} documents, {chunks[chapter]} chunks"
            for chapter, count in documents.items()
        ),
        f"sections with no text left out: {batch.empty_skipped}",
        f"sections without a revision line (version 1, the file date): "
        f"{batch.without_revision_line}",
        f"sections with the issued date as effective date: {batch.effective_date_from_issued}",
        f"file date: {file_date.isoformat()}",
        f"chunk length: longest {max(lengths)}, median {statistics.median(lengths):g}",
    ]


if __name__ == "__main__":
    raise SystemExit(main())
