"""Load issuer-level denial statistics from the CMS Transparency in Coverage PUF (xlsx).

One sheet row is one plan. The issuer-level columns repeat on every plan row of the same
issuer, so the rows are collapsed to one per issuer. A file that fails any quality gate is
rejected outright: nothing is returned. Validated rows are then upserted into
`issuer_denial_stats`, a public reference table (no `account_id`).
"""

import logging
import zlib
from collections.abc import Collection, Mapping, Sequence
from decimal import Decimal
from pathlib import Path
from typing import Annotated, Any, Self
from xml.etree.ElementTree import ParseError
from zipfile import BadZipFile

from openpyxl import load_workbook
from openpyxl.utils.exceptions import InvalidFileException
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)
from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from src.db.base import Base
from src.db.models import (
    ISSUER_COUNT_COLUMNS,
    ISSUER_PERCENT_COLUMNS,
    ExchangeType,
    IssuerDenialStats,
)

logger = logging.getLogger(__name__)

# The sheet has no year column: the plan year appears only in the sheet name.
SHEET_NAME_TEMPLATE = "Transparency {plan_year} - Ind QHP"
HEADER_ROW = 3
# Legend tokens the source uses in place of a number (not available, suppressed, not required, new).
MISSING_TOKENS = frozenset({"*", "**", "***", "N/A"})
YES_NO = {"Yes": True, "No": False}
MAX_PROBLEMS_IN_MESSAGE = 20
UNREADABLE_WORKBOOK = "not a readable .xlsx workbook"
# Raised for damaged content (bad zip or checksum, bad compressed data, broken XML). Sheets
# are read lazily, so these can surface when the file is opened or while rows are read.
DAMAGED_CONTENT_ERRORS = (BadZipFile, zlib.error, ParseError)
# Raised only when opening: also a wrong extension, and a zip without the workbook parts.
UNREADABLE_WORKBOOK_ERRORS = (*DAMAGED_CONTENT_ERRORS, InvalidFileException, KeyError)
UPSERT_CONSTRAINT = "uq_issuer_denial_stats_issuer_year"
UPSERT_KEY_COLUMNS = ("issuer_id", "plan_year")
UPSERT_BATCH_SIZE = 1000

# Row model field -> exact header text in the sheet.
SOURCE_HEADERS = {
    "exchange_type": "Exchange_Type",
    "state": "State",
    "issuer_name": "Issuer_Name",
    "issuer_id": "Issuer_ID",
    "is_new_to_exchange": "Is_Issuer_New_to_Exchange?(Yes_or_No)",
    "claims_received_out_of_network": "Issuer_Claims_Received_Out_of_Network",
    "claims_received_in_network": "Issuer_Claims_Received_In_Network",
    "claims_denied_out_of_network": "Issuer_Claims_Denied_Out_of_Network",
    "claims_denied_in_network": "Issuer_Claims_Denied_In_Network",
    "claims_resubmitted_out_of_network": "Issuer_Claims_Resubmitted_Out_of_Network",
    "claims_resubmitted_in_network": "Issuer_Claims_Resubmitted_In_Network",
    "internal_appeals_filed": "Issuer_Internal_Appeals_Filed",
    "internal_appeals_overturned": "Issuer_Number_Internal_Appeals_Overturned",
    "internal_appeals_overturned_pct": "Issuer_Percent_Internal_Appeals_Overturned",
    "external_appeals_filed": "Issuer_External_Appeals_Filed",
    "external_appeals_overturned": "Issuer_Number_External_Appeals_Overturned",
    "external_appeals_overturned_pct": "Issuer_Percent_External_Appeals_Overturned",
}

NUMERIC_FIELDS = (*ISSUER_COUNT_COLUMNS, *ISSUER_PERCENT_COLUMNS)

Count = Annotated[int | None, Field(ge=0)]
Percent = Annotated[Decimal | None, Field(ge=0, le=100, max_digits=5, decimal_places=2)]


class BatchRejectedError(ValueError):
    """The file failed a quality gate. `problems` lists every reason found."""

    def __init__(self, problems: list[str]) -> None:
        self.problems = problems
        shown = problems[:MAX_PROBLEMS_IN_MESSAGE]
        hidden = len(problems) - len(shown)
        lines = [f"batch rejected, {len(problems)} problem(s):", *shown]
        if hidden:
            lines.append(f"... and {hidden} more")
        super().__init__("\n".join(lines))


class IssuerDenialRow(BaseModel):
    """One validated issuer row, shaped like the `issuer_denial_stats` table."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    plan_year: int = Field(ge=2000, le=2100)
    issuer_id: str = Field(pattern=r"^[0-9]{5}$")
    issuer_name: str = Field(min_length=1, max_length=200)
    state: str = Field(pattern=r"^[A-Z]{2}$")
    exchange_type: ExchangeType
    is_new_to_exchange: bool
    claims_received_out_of_network: Count
    claims_received_in_network: Count
    claims_denied_out_of_network: Count
    claims_denied_in_network: Count
    claims_resubmitted_out_of_network: Count
    claims_resubmitted_in_network: Count
    internal_appeals_filed: Count
    internal_appeals_overturned: Count
    internal_appeals_overturned_pct: Percent
    external_appeals_filed: Count
    external_appeals_overturned: Count
    external_appeals_overturned_pct: Percent

    @field_validator("issuer_id", mode="before")
    @classmethod
    def _issuer_id_as_text(cls, value: Any) -> Any:
        # The sheet stores the id as a number.
        if isinstance(value, int) and not isinstance(value, bool):
            return str(value)
        return value

    @field_validator("is_new_to_exchange", mode="before")
    @classmethod
    def _yes_no_to_bool(cls, value: Any) -> bool:
        if isinstance(value, bool):
            return value
        if value not in YES_NO:
            raise ValueError("must be 'Yes' or 'No'")
        return YES_NO[value]

    @field_validator(*NUMERIC_FIELDS, mode="before")
    @classmethod
    def _missing_token_to_none(cls, value: Any) -> Any:
        if isinstance(value, bool):
            raise ValueError("must be a number")  # otherwise TRUE would be read as 1
        if isinstance(value, str) and value in MISSING_TOKENS:
            return None
        return value

    @field_validator(*ISSUER_PERCENT_COLUMNS, mode="before")
    @classmethod
    def _float_to_exact_decimal(cls, value: Any) -> Any:
        if isinstance(value, float):
            return Decimal(str(value))
        return value

    @model_validator(mode="after")
    def _overturned_not_above_filed(self) -> Self:
        for kind in ("internal", "external"):
            filed = getattr(self, f"{kind}_appeals_filed")
            overturned = getattr(self, f"{kind}_appeals_overturned")
            if filed is not None and overturned is not None and overturned > filed:
                raise ValueError(f"{kind} appeals overturned is greater than {kind} appeals filed")
        return self


def read_sheet_rows(
    path: Path, plan_year: int, headers: Mapping[str, str]
) -> list[tuple[int, dict[str, Any]]]:
    """Return (row number, {field: cell}) for every non-blank data row of the plan year's sheet.

    `headers` maps a field name to the exact header text of its column. The sheet read is the
    one named for `plan_year`, so a year that does not match the file is rejected. Raises
    `BatchRejectedError` for an unreadable file, a missing sheet or header, or no data rows.
    Reading the full file takes a few seconds.
    """
    sheet_name = SHEET_NAME_TEMPLATE.format(plan_year=plan_year)
    try:
        workbook = load_workbook(path, read_only=True, data_only=True)
    except UNREADABLE_WORKBOOK_ERRORS as exc:
        raise BatchRejectedError([UNREADABLE_WORKBOOK]) from exc
    except OSError as exc:
        # openpyxl raises a bare OSError for an Office file that is not a spreadsheet.
        # Real operating-system errors (such as permission denied) carry an errno: let them through.
        if exc.errno is not None:
            raise
        raise BatchRejectedError([UNREADABLE_WORKBOOK]) from exc
    try:
        if sheet_name not in workbook.sheetnames:
            raise BatchRejectedError([f"sheet {sheet_name!r} not found"])
        sheet_rows = workbook[sheet_name].iter_rows(min_row=HEADER_ROW, values_only=True)
        header = next(sheet_rows, ())
        positions = {text: index for index, text in enumerate(header)}
        missing = [text for text in headers.values() if text not in positions]
        if missing:
            raise BatchRejectedError(
                [f"row {HEADER_ROW}: missing header {text!r}" for text in missing]
            )

        rows: list[tuple[int, dict[str, Any]]] = []
        for row_number, cells in enumerate(sheet_rows, start=HEADER_ROW + 1):
            if all(cell is None for cell in cells):
                continue  # the sheet ends with a long run of blank rows
            padded = cells + (None,) * (len(header) - len(cells))
            rows.append(
                (row_number, {field: padded[positions[text]] for field, text in headers.items()})
            )
    except DAMAGED_CONTENT_ERRORS as exc:
        raise BatchRejectedError([UNREADABLE_WORKBOOK]) from exc
    finally:
        workbook.close()

    if not rows:
        raise BatchRejectedError(["no data rows"])
    return rows


def read_issuer_denial_rows(path: Path, plan_year: int) -> list[IssuerDenialRow]:
    """Read the PUF workbook at `path` and return one validated row per issuer.

    Raises `BatchRejectedError` if any quality gate fails (see `read_sheet_rows` for the
    file-level ones). Reading the full file takes a few seconds.
    """
    problems: list[str] = []
    by_issuer: dict[str, IssuerDenialRow] = {}
    for row_number, raw in read_sheet_rows(path, plan_year, SOURCE_HEADERS):
        # The source never leaves a number blank (it uses a legend token), so a blank
        # means a damaged row, not a missing value.
        blank = [field for field in NUMERIC_FIELDS if raw[field] is None]
        if blank:
            problems.extend(f"row {row_number}: {field}: blank cell" for field in blank)
            continue
        try:
            row = IssuerDenialRow.model_validate({"plan_year": plan_year, **raw})
        except ValidationError as exc:
            problems.extend(describe_validation_error(row_number, exc))
            continue
        earlier = by_issuer.setdefault(row.issuer_id, row)
        if earlier != row:
            problems.append(
                f"row {row_number}: issuer {row.issuer_id} differs from an earlier row "
                "of the same issuer"
            )
    if problems:
        raise BatchRejectedError(problems)

    rows = list(by_issuer.values())
    _warn_denied_above_received(rows)
    logger.info("read %d issuer rows for plan year %d", len(rows), plan_year)
    return rows


def upsert_rows(
    session: Session,
    table: type[Base],
    rows: Sequence[BaseModel],
    constraint: str,
    key_columns: Collection[str],
) -> int:
    """Insert `rows` into `table`, updating any row that already has the same key.

    `constraint` is the unique constraint that defines the key and `key_columns` its columns.
    Every other field of the row model is overwritten and `updated_at` is set. Rows are sent
    in batches, because one statement can carry at most 65,535 values. Returns the number of
    rows written. Does not commit: the caller owns the transaction.
    """
    for start in range(0, len(rows), UPSERT_BATCH_SIZE):
        batch = rows[start : start + UPSERT_BATCH_SIZE]
        statement = insert(table).values([row.model_dump() for row in batch])
        new_values = {
            name: statement.excluded[name]
            for name in type(batch[0]).model_fields
            if name not in key_columns
        }
        session.execute(
            statement.on_conflict_do_update(
                constraint=constraint, set_={**new_values, "updated_at": func.now()}
            )
        )
    return len(rows)


def upsert_issuer_denial_rows(session: Session, rows: list[IssuerDenialRow]) -> int:
    """Insert `rows` into `issuer_denial_stats`, updating any issuer + plan year already there.

    Loading the same rows twice changes nothing except `updated_at`. Returns the number of
    rows written. Does not commit: the caller owns the transaction.
    """
    written = upsert_rows(session, IssuerDenialStats, rows, UPSERT_CONSTRAINT, UPSERT_KEY_COLUMNS)
    logger.info("upserted %d issuer rows", written)
    return written


def describe_validation_error(row_number: int, exc: ValidationError) -> list[str]:
    """One line per failed field. Cell values are left out on purpose."""
    return [
        f"row {row_number}: {'.'.join(str(part) for part in error['loc']) or 'row'}: {error['msg']}"
        for error in exc.errors()
    ]


def _warn_denied_above_received(rows: list[IssuerDenialRow]) -> None:
    # Seen in the real file (possibly different counting dates), so this warns instead of rejecting.
    odd = [
        row.issuer_id
        for row in rows
        if row.claims_denied_out_of_network is not None
        and row.claims_received_out_of_network is not None
        and row.claims_denied_out_of_network > row.claims_received_out_of_network
    ]
    if odd:
        logger.warning(
            "%d issuer(s) report more out-of-network claims denied than received: %s",
            len(odd),
            ", ".join(odd),
        )
