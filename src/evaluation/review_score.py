"""Reads the score file a reviewer filled in, checks it, and works out the acceptance share.

The score file is a CSV with one row per sampled draft: the claim id, the fingerprint of the
draft the row was written for, the verdict, the reason of a `reject`, and a free note. The
reviewer is a person; nothing here scores a draft.

The acceptance share is the drafts with the verdict `accept` or `accept_light_edits` over all
scored drafts. It is checked against the Stage 4 done condition (docs/05-build-plan.md), at
least 60%. The drafts are made up, one person scores a small sample, and the share says
nothing about real appeals.
"""

import csv
import io
from collections.abc import Mapping, Sequence
from decimal import ROUND_DOWN, ROUND_HALF_EVEN, Decimal
from pathlib import Path
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from src.evaluation.report import NO_VALUE, GateStatus
from src.evaluation.review_rubric import (
    ACCEPTANCE_TARGET,
    ACCEPTED_VERDICTS,
    RejectReason,
    Verdict,
)
from src.ml.labels import ClaimId

SCORE_COLUMNS = ("claim_id", "draft_sha256", "verdict", "reject_reason", "note")
# The first line holds the column names, so the first score row is line 2.
FIRST_ROW_LINE = 2
MAX_NOTE_CHARACTERS = 1000
# A share is printed as a percentage with two decimals.
_SHARE_STEP = Decimal("0.0001")


class ScoreFileError(Exception):
    """The score file cannot be used. `problems` says why, one plain line each.

    A problem names a line of the file and never quotes a value from it.
    """

    def __init__(self, problems: Sequence[str]) -> None:
        super().__init__(problems[0])
        self.problems = tuple(problems)


class ReviewScore(BaseModel):
    """One row of the score file. `verdict` is None while the row is not filled in."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    line: int = Field(ge=FIRST_ROW_LINE)
    claim_id: ClaimId
    draft_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    verdict: Verdict | None
    reject_reason: RejectReason | None
    # Left out of the repr: a note is free text.
    note: str = Field(default="", max_length=MAX_NOTE_CHARACTERS, repr=False)

    @model_validator(mode="after")
    def _reason_fits_verdict(self) -> Self:
        if self.verdict is Verdict.REJECT and self.reject_reason is None:
            raise ValueError("a reject needs a reject_reason")
        if self.verdict is not Verdict.REJECT and self.reject_reason is not None:
            raise ValueError("only a reject has a reject_reason")
        return self


class ShareCount(BaseModel):
    """How many of a group of scored drafts were accepted."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    accepted: int = Field(ge=0)
    scored: int = Field(ge=0)

    @model_validator(mode="after")
    def _accepted_within_scored(self) -> Self:
        if self.accepted > self.scored:
            raise ValueError("more drafts accepted than scored")
        return self

    @property
    def share(self) -> Decimal | None:
        """The accepted share, 0 to 1; None for a group with no draft."""
        return Decimal(self.accepted) / Decimal(self.scored) if self.scored else None


class ReviewReport(BaseModel):
    """What one finished review measured."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    verdicts: dict[Verdict, int]
    reject_reasons: dict[RejectReason, int]
    with_citation: ShareCount
    without_citation: ShareCount

    @property
    def overall(self) -> ShareCount:
        return ShareCount(
            accepted=self.with_citation.accepted + self.without_citation.accepted,
            scored=self.with_citation.scored + self.without_citation.scored,
        )

    @property
    def target_met(self) -> bool:
        """Whether the acceptance share is at least the target. Exactly the target is met."""
        share = self.overall.share
        return share is not None and share >= ACCEPTANCE_TARGET

    def as_text(self) -> str:
        """The report as printed: counts and shares only, never a claim id or any text."""
        status = GateStatus.MET if self.target_met else GateStatus.BELOW
        lines = [f"scored {self.overall.scored} drafts (acceptance target {ACCEPTANCE_TARGET:.2%})"]
        for verdict in Verdict:
            lines.append(f"  {verdict.value}: {self.verdicts[verdict]}")
        for reason in RejectReason:
            lines.append(f"    {reason.value}: {self.reject_reasons[reason]}")
        lines.append(
            f"  acceptance share: {_count(self.overall, ACCEPTANCE_TARGET)}, {status.value}"
        )
        lines.append(f"  drafts with a citation: {_count(self.with_citation)}")
        lines.append(f"  drafts without a citation: {_count(self.without_citation)}")
        return "\n".join(lines)


def read_review_scores(path: Path) -> tuple[ReviewScore, ...]:
    """Read the score file at `path`, in file order.

    A row whose verdict cell is empty is returned with no verdict. Raises `ScoreFileError`
    with every problem found: a file that cannot be read, a first line that is not the
    column names, a row with the wrong number of values, a verdict or reason outside the
    rubric, a `reject` without a reason, a reason on another verdict, a claim scored twice,
    or no row at all.
    """
    try:
        # `utf-8-sig`: a spreadsheet program may put a byte-order mark first.
        with path.open(encoding="utf-8-sig", newline="") as file:
            text = file.read()
    except (OSError, UnicodeDecodeError) as error:
        raise ScoreFileError([f"the score file cannot be read ({type(error).__name__})"]) from None
    try:
        rows = list(csv.reader(io.StringIO(text, newline="")))
    except csv.Error:
        raise ScoreFileError(["the score file is not a CSV file"]) from None
    if not rows or tuple(rows[0]) != SCORE_COLUMNS:
        raise ScoreFileError([f"the first line must be: {','.join(SCORE_COLUMNS)}"])

    problems: list[str] = []
    scores: list[ReviewScore] = []
    first_line_of: dict[str, int] = {}
    for line, cells in enumerate(rows[1:], start=FIRST_ROW_LINE):
        if not any(cell.strip() for cell in cells):
            continue  # an empty line, often the last one
        if len(cells) != len(SCORE_COLUMNS):
            problems.append(
                f"line {line}: {len(SCORE_COLUMNS)} values expected, {len(cells)} found"
            )
            continue
        claim_id, draft_sha256, verdict, reject_reason, note = (cell.strip() for cell in cells)
        try:
            score = ReviewScore.model_validate(
                {
                    "line": line,
                    "claim_id": claim_id,
                    "draft_sha256": draft_sha256,
                    "verdict": verdict or None,
                    "reject_reason": reject_reason or None,
                    "note": note,
                }
            )
        except ValidationError as error:
            # Without the input: a problem never quotes a value from the file.
            for found in error.errors(include_input=False):
                column = ".".join(str(part) for part in found["loc"])
                message = found["msg"].removeprefix("Value error, ")
                problems.append(
                    f"line {line}: {column}: {message}" if column else f"line {line}: {message}"
                )
            continue
        if score.claim_id in first_line_of:
            problems.append(
                f"line {line}: the claim is already scored on line {first_line_of[score.claim_id]}"
            )
            continue
        first_line_of[score.claim_id] = line
        scores.append(score)
    if not problems and not scores:
        problems.append("the score file holds no row")
    if problems:
        raise ScoreFileError(problems)
    return tuple(scores)


def check_review_scores(
    scores: Sequence[ReviewScore], fingerprints: Mapping[str, str], *, size: int
) -> list[str]:
    """Return what is wrong with `scores` against the stored drafts; empty when nothing is.

    `fingerprints` holds the fingerprint of the stored draft of every claim that can be
    reviewed, and `size` how many drafts were sampled. Found: a number of rows other than
    `size` (a row was removed or added), a row for a claim with no draft that can be
    reviewed, and a row written for a draft that was drafted again since.
    """
    problems = []
    if len(scores) != size:
        problems.append(
            f"{len(scores)} rows found, {size} expected: every sampled draft is scored exactly"
            " once"
        )
    for score in scores:
        stored = fingerprints.get(score.claim_id)
        if stored is None:
            problems.append(
                f"line {score.line}: no draft that can be reviewed is stored for this claim"
            )
        elif stored != score.draft_sha256:
            problems.append(
                f"line {score.line}: the stored draft is not the one this row was written for"
                " (it was drafted again): export the sample again"
            )
    return problems


def count_without_verdict(scores: Sequence[ReviewScore]) -> int:
    """Return how many of `scores` are not filled in yet."""
    return sum(score.verdict is None for score in scores)


def build_review_report(
    scores: Sequence[ReviewScore], has_citation: Mapping[str, bool]
) -> ReviewReport:
    """Count the verdicts of a finished review.

    `has_citation` says, per claim id, whether its draft carries at least one citation; it
    must hold every scored claim. Raises `ValueError` when `scores` is empty or a row has no
    verdict: a share is never worked out from a review that is not finished.
    """
    if not scores:
        raise ValueError("at least one score is needed")
    verdicts = dict.fromkeys(Verdict, 0)
    reject_reasons = dict.fromkeys(RejectReason, 0)
    accepted = {True: 0, False: 0}
    scored = {True: 0, False: 0}
    for score in scores:
        if score.verdict is None:
            raise ValueError("a score has no verdict yet")
        verdicts[score.verdict] += 1
        if score.reject_reason is not None:
            reject_reasons[score.reject_reason] += 1
        cited = has_citation[score.claim_id]
        scored[cited] += 1
        accepted[cited] += score.verdict in ACCEPTED_VERDICTS
    return ReviewReport(
        verdicts=verdicts,
        reject_reasons=reject_reasons,
        with_citation=ShareCount(accepted=accepted[True], scored=scored[True]),
        without_citation=ShareCount(accepted=accepted[False], scored=scored[False]),
    )


def _count(count: ShareCount, target: Decimal | None = None) -> str:
    share = count.share
    if share is None:
        return f"{count.accepted} of {count.scored} ({NO_VALUE})"
    rounded = share.quantize(_SHARE_STEP, rounding=ROUND_HALF_EVEN)
    if target is not None and share < target <= rounded:
        # Never print a share that looks like it meets a target it misses.
        rounded = share.quantize(_SHARE_STEP, rounding=ROUND_DOWN)
    return f"{count.accepted} of {count.scored} ({rounded:.2%})"
