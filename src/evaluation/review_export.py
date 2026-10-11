"""Builds the two review files: the one a reviewer reads and the score file to fill in.

Both are plain text built from `ReviewItem`s, so the same sampled drafts always give the same
files. The file to read is Markdown: the rubric, then per draft the draft, the denial letter
document and every cited chunk in full. The score file is a CSV with one empty row per draft.

The file to read holds document text and letter text. It is written under a git-ignored
folder by the caller and never printed. Everything in it is made up, apart from the public
policy text.
"""

import csv
import io
import re
from collections.abc import Sequence

from src.drafting.schemas import ParagraphKind
from src.evaluation.review_rubric import (
    ACCEPTANCE_TARGET,
    REJECT_REASON_MEANINGS,
    VERDICT_MEANINGS,
)
from src.evaluation.review_score import SCORE_COLUMNS
from src.evaluation.review_store import ReviewEvidence, ReviewItem

REVIEW_FILE_NAME = "review_drafts.md"
SCORES_FILE_NAME = "review_scores.csv"
_MIN_FENCE = 3


def build_review_text(items: Sequence[ReviewItem]) -> str:
    """Return the Markdown file a reviewer reads: the rubric, then every draft of `items`."""
    lines = [
        "# Draft review",
        "",
        f"{len(items)} drafted appeal letters to score. The claims, the people and the"
        " documents are made up; the policy text is public. Nothing here is ever sent to an"
        " insurer.",
        "",
        f"For each draft, read the draft, the denial letter document it answers and the"
        f" evidence it cites. Then fill in its row in `{SCORES_FILE_NAME}`: a `verdict`, a"
        " `reject_reason` when the verdict is `reject`, and a `note` if you like. Do not"
        " change the `claim_id` and `draft_sha256` columns.",
        "",
        "## The rubric",
        "",
        "Would you send this letter?",
        "",
    ]
    lines += [f"- `{verdict.value}`: {meaning}" for verdict, meaning in VERDICT_MEANINGS.items()]
    lines += ["", "A `reject` needs one reason:", ""]
    lines += [
        f"- `{reason.value}`: {meaning}" for reason, meaning in REJECT_REASON_MEANINGS.items()
    ]
    lines += [
        "",
        "A draft with no citation is judged by the same rubric. The target is"
        f" {ACCEPTANCE_TARGET:.0%} of the drafts with `accept` or `accept_light_edits`.",
    ]
    for number, item in enumerate(items, start=1):
        lines += ["", "---", "", *_draft_lines(item, number, len(items))]
    return "\n".join(lines) + "\n"


def build_scores_csv(items: Sequence[ReviewItem]) -> str:
    """Return the score file to fill in: the column names and one row per draft of `items`.

    A row carries its claim id and the fingerprint of the draft; the other cells are empty.
    """
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(SCORE_COLUMNS)
    for item in items:
        writer.writerow([item.source_claim_id, item.draft_sha256, "", "", ""])
    return buffer.getvalue()


def _draft_lines(item: ReviewItem, number: int, total: int) -> list[str]:
    evidence_number = {
        (found.source, found.source_document_id, found.chunk_index): position
        for position, found in enumerate(item.evidence, start=1)
    }
    lines = [f"## Draft {number} of {total}: claim {item.source_claim_id}", "", "### The draft"]
    for position, paragraph in enumerate(item.letter.paragraphs, start=1):
        is_policy = paragraph.kind is ParagraphKind.POLICY
        heading = f"Paragraph {position}" + (" (policy statement)" if is_policy else "")
        lines += ["", f"**{heading}**", "", *_fenced(paragraph.text)]
        for citation in paragraph.citations:
            key = (citation.source, citation.source_document_id, citation.chunk_index)
            lines += ["", f"Cites evidence {evidence_number[key]}, with this quote:", ""]
            lines += _fenced(citation.quote)
    lines += ["", "### The denial letter document", "", *_fenced(item.document_text)]
    lines += ["", "### The cited evidence"]
    if not item.evidence:
        lines += ["", "This draft cites no evidence."]
    for position, found in enumerate(item.evidence, start=1):
        lines += ["", f"**Evidence {position}: {_evidence_title(found)}**", ""]
        lines += _fenced(found.text)
    return lines


def _evidence_title(found: ReviewEvidence) -> str:
    title = f"{found.title}, section {found.section_number}"
    return f"{title} ({found.section_title})" if found.section_title else title


def _fenced(text: str) -> list[str]:
    """`text` as a Markdown code block, shown as it is, whatever characters it holds."""
    longest = max((len(run) for run in re.findall(r"`+", text)), default=0)
    fence = "`" * max(_MIN_FENCE, longest + 1)
    return [f"{fence}text", text, fence]
