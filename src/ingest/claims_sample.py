"""Load synthetic claims from the CMS DE-SynPUF carrier claims file (a zip holding one csv).

One csv row is one claim with up to 13 service lines side by side (`HCPCS_CD_1` to
`HCPCS_CD_13`, and so on). Each row becomes one claim and one row per used line. A file that
fails any quality gate is rejected outright: nothing is returned. Validated rows are then
upserted into `claim_samples` and `claim_sample_lines`, public reference tables (no
`account_id`). The source is fully synthetic; the beneficiary, physician and tax identifier
columns are not kept.
"""

import csv
import io
import logging
import re
import zlib
from collections.abc import Generator, Sequence
from contextlib import closing
from datetime import date, datetime
from decimal import Decimal
from itertools import islice
from pathlib import Path
from typing import Annotated, Any, Self
from zipfile import BadZipFile, ZipFile

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)
from sqlalchemy.orm import Session

from src.db.models import CLAIM_LINE_MAX_NUMBER, ClaimSample, ClaimSampleLine
from src.ingest.marketplace_denials import (
    BatchRejectedError,
    describe_validation_error,
    upsert_rows,
)

logger = logging.getLogger(__name__)

CLAIM_ID_HEADER = "CLM_ID"
FROM_DATE_HEADER = "CLM_FROM_DT"
THRU_DATE_HEADER = "CLM_THRU_DT"
CLAIM_DIAGNOSIS_FAMILY = "ICD9_DGNS_CD"
CLAIM_DIAGNOSIS_SLOTS = 8
TAX_NUMBER_FAMILY = "TAX_NUM"
HCPCS_FAMILY = "HCPCS_CD"
INDICATOR_FAMILY = "LINE_PRCSG_IND_CD"
LINE_DIAGNOSIS_FAMILY = "LINE_ICD9_DGNS_CD"
# Line model field -> column family in the file (each family has one column per line slot).
AMOUNT_FAMILIES = {
    "payment_amount": "LINE_NCH_PMT_AMT",
    "deductible_amount": "LINE_BENE_PTB_DDCTBL_AMT",
    "primary_payer_paid_amount": "LINE_BENE_PRMRY_PYR_PD_AMT",
    "coinsurance_amount": "LINE_COINSRNC_AMT",
    "allowed_charge_amount": "LINE_ALOWD_CHRG_AMT",
}
# Families that hold text; an unused line slot must leave them empty.
LINE_CODE_FAMILIES = ("PRF_PHYSN_NPI", HCPCS_FAMILY, LINE_DIAGNOSIS_FAMILY)
# The per-line families in the order the file lists them.
LINE_FAMILIES = (
    "PRF_PHYSN_NPI",
    TAX_NUMBER_FAMILY,
    HCPCS_FAMILY,
    *AMOUNT_FAMILIES.values(),
    INDICATOR_FAMILY,
    LINE_DIAGNOSIS_FAMILY,
)
LINE_SLOTS = range(1, CLAIM_LINE_MAX_NUMBER + 1)
EXPECTED_HEADER = (
    "DESYNPUF_ID",
    CLAIM_ID_HEADER,
    FROM_DATE_HEADER,
    THRU_DATE_HEADER,
    *(f"{CLAIM_DIAGNOSIS_FAMILY}_{slot}" for slot in range(1, CLAIM_DIAGNOSIS_SLOTS + 1)),
    *(f"{family}_{slot}" for family in LINE_FAMILIES for slot in LINE_SLOTS),
)
COLUMN = {name: index for index, name in enumerate(EXPECTED_HEADER)}

SOURCE_DATE_FORMAT = "%Y%m%d"
SOURCE_DATE_PATTERN = re.compile(r"[0-9]{8}")
# The file writes every amount as plain digits with two decimals, such as 50.00. Anything
# else a number parser would accept (1e2, +50.00, 5_0.00, 50) means a damaged cell.
SOURCE_AMOUNT_PATTERN = re.compile(r"[0-9]+\.[0-9]{2}")
# The file covers claims from 2008 to 2010.
MIN_CLAIM_DATE = date(2008, 1, 1)
MAX_CLAIM_DATE = date(2010, 12, 31)
# The one-character values the CMS codebook defines for the line processing indicator
# (`A` is allowed). The published file also uses values the codebook does not list.
CODEBOOK_INDICATORS = frozenset("ABCDILMNOPQRSTUVXYZ!@#$*()+<>%&")
# Stop reading once this many problems are found: a broken column fails on every row.
MAX_PROBLEMS_COLLECTED = 1000
HEADER_ROW = 1
UNREADABLE_ZIP = "not a readable .zip file"
# Raised for damaged content. The csv is read as a stream, so these can surface when the
# file is opened or while rows are read. zipfile raises RuntimeError for an encrypted member
# and NotImplementedError (a RuntimeError) for a compression method it does not support.
DAMAGED_ZIP_ERRORS = (BadZipFile, zlib.error, EOFError, RuntimeError)
CLAIM_UPSERT_CONSTRAINT = "uq_claim_samples_source_claim_id"
CLAIM_UPSERT_KEY_COLUMNS = ("source_claim_id",)
LINE_UPSERT_CONSTRAINT = "uq_claim_sample_lines_claim_line"
LINE_UPSERT_KEY_COLUMNS = ("source_claim_id", "line_number")

Amount = Annotated[Decimal, Field(ge=0, max_digits=12, decimal_places=2)]
ClaimId = Annotated[str, Field(pattern=r"^[0-9]{15}$")]
DiagnosisCode = Annotated[str, Field(min_length=1, max_length=5)]
ClaimDate = Annotated[date, Field(ge=MIN_CLAIM_DATE, le=MAX_CLAIM_DATE)]


class ClaimSampleRow(BaseModel):
    """One validated claim, shaped like the `claim_samples` table."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    source_claim_id: ClaimId
    claim_from_date: ClaimDate
    claim_thru_date: ClaimDate
    diagnosis_codes: list[DiagnosisCode]

    @field_validator("claim_from_date", "claim_thru_date", mode="before")
    @classmethod
    def _source_text_to_date(cls, value: Any) -> Any:
        # The file writes dates as 8 digits, which would otherwise be read as a timestamp.
        if isinstance(value, str):
            if not SOURCE_DATE_PATTERN.fullmatch(value):
                raise ValueError("must be a date written as YYYYMMDD")
            try:
                return datetime.strptime(value, SOURCE_DATE_FORMAT).date()
            except ValueError:
                raise ValueError("must be a date written as YYYYMMDD") from None
        return value

    @model_validator(mode="after")
    def _from_not_after_thru(self) -> Self:
        if self.claim_from_date > self.claim_thru_date:
            raise ValueError("claim from date is after claim thru date")
        return self


class ClaimSampleLineRow(BaseModel):
    """One validated service line, shaped like the `claim_sample_lines` table.

    The amounts are kept as published: they do not have to add up.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    source_claim_id: ClaimId
    line_number: int = Field(ge=1, le=CLAIM_LINE_MAX_NUMBER)
    hcpcs_code: str | None = Field(min_length=5, max_length=5)
    line_diagnosis_code: DiagnosisCode | None
    processing_indicator: str = Field(min_length=1, max_length=1)
    payment_amount: Amount
    deductible_amount: Amount
    primary_payer_paid_amount: Amount
    coinsurance_amount: Amount
    allowed_charge_amount: Amount

    @field_validator(*AMOUNT_FAMILIES, mode="before")
    @classmethod
    def _source_text_is_a_plain_amount(cls, value: Any) -> Any:
        if isinstance(value, str) and not SOURCE_AMOUNT_PATTERN.fullmatch(value):
            raise ValueError("must be a number written with two decimals, such as 50.00")
        return value


class ClaimSampleBatch(BaseModel):
    """The claims read from one file, with their service lines."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    claims: list[ClaimSampleRow]
    lines: list[ClaimSampleLineRow]


def read_claim_sample_rows(path: Path, max_claims: int | None = None) -> ClaimSampleBatch:
    """Read the carrier claims zip at `path` and return its validated claims and lines.

    `max_claims` (1 or more) stops after that many csv rows; the quality gates then cover
    only the rows read. Raises `BatchRejectedError` if any gate fails. Reading 50,000 claims
    takes about ten seconds; the full file (2.4 million claims) takes minutes and several
    gigabytes of memory.
    """
    if max_claims is not None and max_claims < 1:
        raise ValueError("max_claims must be 1 or more")
    problems: list[str] = []
    claims: list[ClaimSampleRow] = []
    lines: list[ClaimSampleLineRow] = []
    seen: set[str] = set()
    rows_read = 0
    # Closing the generator closes the zip, also when reading stops before the end.
    with closing(_csv_rows(path)) as rows:
        for row_number, cells in islice(rows, max_claims):
            rows_read += 1
            if len(problems) >= MAX_PROBLEMS_COLLECTED:
                break
            if len(cells) != len(EXPECTED_HEADER):
                problems.append(
                    f"row {row_number}: has {len(cells)} fields, expected {len(EXPECTED_HEADER)}"
                )
                continue
            if any(cell != cell.strip() for cell in cells):
                # Otherwise a cell holding only spaces would count as filled.
                problems.append(f"row {row_number}: a cell has leading or trailing whitespace")
                continue
            used = [slot for slot in LINE_SLOTS if _used(cells, slot)]
            layout_problems = _line_layout_problems(cells, used)
            if layout_problems:
                problems.extend(f"row {row_number}: {problem}" for problem in layout_problems)
                continue
            try:
                claim = ClaimSampleRow.model_validate(_claim_fields(cells))
            except ValidationError as exc:
                problems.extend(describe_validation_error(row_number, exc))
                continue
            row_lines, line_problems = _validated_lines(cells, used, row_number)
            if line_problems:
                problems.extend(line_problems)
                continue
            if claim.source_claim_id in seen:
                problems.append(f"row {row_number}: claim id appears more than once in the file")
                continue
            seen.add(claim.source_claim_id)
            claims.append(claim)
            lines.extend(row_lines)
    if not rows_read:
        problems.append("no data rows")
    if problems:
        raise BatchRejectedError(problems)

    _warn_implausible(lines)
    logger.info("read %d claims with %d service lines", len(claims), len(lines))
    return ClaimSampleBatch(claims=claims, lines=lines)


def upsert_claim_sample_batch(session: Session, batch: ClaimSampleBatch) -> tuple[int, int]:
    """Insert the batch into `claim_samples` and `claim_sample_lines`, updating rows already there.

    Claims are matched on their source claim id, lines on claim id and line number; the claims
    are written first because the lines point at them. Loading the same batch twice changes
    nothing except `updated_at`. Rows are never removed. Returns (claims, lines) written.
    Does not commit: the caller owns the transaction. Writing 50,000 claims takes about
    half a minute.
    """
    claims = upsert_rows(
        session, ClaimSample, batch.claims, CLAIM_UPSERT_CONSTRAINT, CLAIM_UPSERT_KEY_COLUMNS
    )
    lines = upsert_rows(
        session, ClaimSampleLine, batch.lines, LINE_UPSERT_CONSTRAINT, LINE_UPSERT_KEY_COLUMNS
    )
    logger.info("upserted %d claims and %d service lines", claims, lines)
    return claims, lines


def _csv_rows(path: Path) -> Generator[tuple[int, list[str]], None, None]:
    """Yield (row number, cells) for every data row of the one csv inside the zip.

    Raises `BatchRejectedError` for an unreadable zip, a zip that does not hold exactly one
    csv, text that is not UTF-8, or a header that differs from the expected one.
    """
    try:
        with ZipFile(path) as archive:
            names = archive.namelist()
            if len(names) != 1 or not names[0].lower().endswith(".csv"):
                raise BatchRejectedError(["the zip must hold exactly one .csv file"])
            with (
                archive.open(names[0]) as raw,
                io.TextIOWrapper(raw, encoding="utf-8", newline="") as text,
            ):
                reader = csv.reader(text)
                header_problems = _header_problems(next(reader, []))
                if header_problems:
                    raise BatchRejectedError(header_problems)
                yield from enumerate(reader, start=HEADER_ROW + 1)
    except DAMAGED_ZIP_ERRORS as exc:
        raise BatchRejectedError([UNREADABLE_ZIP]) from exc
    except UnicodeDecodeError as exc:
        raise BatchRejectedError(["the csv is not valid UTF-8 text"]) from exc
    except csv.Error as exc:
        raise BatchRejectedError(["the csv cannot be parsed"]) from exc


def _header_problems(header: Sequence[str]) -> list[str]:
    if len(header) != len(EXPECTED_HEADER):
        return [
            f"row {HEADER_ROW}: header has {len(header)} columns, expected {len(EXPECTED_HEADER)}"
        ]
    return [
        f"row {HEADER_ROW}: column {index} should be {expected!r}"
        for index, (found, expected) in enumerate(zip(header, EXPECTED_HEADER, strict=True), 1)
        if found != expected
    ]


def _cell(cells: Sequence[str], family: str, slot: int) -> str:
    return cells[COLUMN[f"{family}_{slot}"]]


def _used(cells: Sequence[str], slot: int) -> bool:
    # In the published file a line slot is in use exactly when it has a tax number, which is
    # also exactly when it has an indicator. The procedure code is often empty on a used line.
    return bool(_cell(cells, TAX_NUMBER_FAMILY, slot) or _cell(cells, INDICATOR_FAMILY, slot))


def _is_zero(text: str) -> bool:
    return bool(SOURCE_AMOUNT_PATTERN.fullmatch(text)) and Decimal(text) == 0


def _line_layout_problems(cells: Sequence[str], used: list[int]) -> list[str]:
    """Problems with which line slots are filled. Cell values are left out on purpose."""
    if not used:
        return ["no used service line"]
    problems: list[str] = []
    if used != list(range(1, len(used) + 1)):
        problems.append("used service lines are not in a row starting at line 1")
    for slot in LINE_SLOTS:
        if slot in used:
            if not _cell(cells, TAX_NUMBER_FAMILY, slot):
                problems.append(f"line {slot}: indicator without a tax number")
            if not _cell(cells, INDICATOR_FAMILY, slot):
                problems.append(f"line {slot}: tax number without an indicator")
        elif any(_cell(cells, family, slot) for family in LINE_CODE_FAMILIES) or not all(
            _is_zero(_cell(cells, family, slot)) for family in AMOUNT_FAMILIES.values()
        ):
            problems.append(f"line {slot}: values in a line with no tax number and no indicator")
    return problems


def _claim_fields(cells: Sequence[str]) -> dict[str, Any]:
    diagnosis_cells = (
        _cell(cells, CLAIM_DIAGNOSIS_FAMILY, slot) for slot in range(1, CLAIM_DIAGNOSIS_SLOTS + 1)
    )
    return {
        "source_claim_id": cells[COLUMN[CLAIM_ID_HEADER]],
        "claim_from_date": cells[COLUMN[FROM_DATE_HEADER]],
        "claim_thru_date": cells[COLUMN[THRU_DATE_HEADER]],
        # The slots can have gaps, so a slot's position means nothing: empty ones are dropped.
        "diagnosis_codes": [code for code in diagnosis_cells if code],
    }


def _validated_lines(
    cells: Sequence[str], used: list[int], row_number: int
) -> tuple[list[ClaimSampleLineRow], list[str]]:
    lines: list[ClaimSampleLineRow] = []
    problems: list[str] = []
    for slot in used:
        fields: dict[str, Any] = {
            "source_claim_id": cells[COLUMN[CLAIM_ID_HEADER]],
            "line_number": slot,
            "hcpcs_code": _cell(cells, HCPCS_FAMILY, slot) or None,
            "line_diagnosis_code": _cell(cells, LINE_DIAGNOSIS_FAMILY, slot) or None,
            "processing_indicator": _cell(cells, INDICATOR_FAMILY, slot),
            **{field: _cell(cells, family, slot) for field, family in AMOUNT_FAMILIES.items()},
        }
        try:
            lines.append(ClaimSampleLineRow.model_validate(fields))
        except ValidationError as exc:
            problems.extend(
                f"row {row_number}: line {slot}: {error['loc'][0]}: {error['msg']}"
                for error in exc.errors()
            )
    return lines, problems


def _warn_implausible(lines: list[ClaimSampleLineRow]) -> None:
    # These look wrong but occur in the published file (CMS did no cleaning and altered the
    # amounts for privacy), so they are logged once each instead of rejecting the batch.
    unknown = sorted(
        {line.processing_indicator for line in lines} - CODEBOOK_INDICATORS  # codes, not people
    )
    if unknown:
        count = sum(line.processing_indicator in unknown for line in lines)
        logger.warning(
            "%d line(s) use a processing indicator that is not in the CMS codebook: %s",
            count,
            " ".join(unknown),
        )
    above = sum(line.payment_amount > line.allowed_charge_amount for line in lines)
    if above:
        logger.warning("%d line(s) have a payment above the allowed charge", above)
    no_code = sum(line.hcpcs_code is None for line in lines)
    if no_code:
        logger.warning("%d line(s) have no procedure code", no_code)
