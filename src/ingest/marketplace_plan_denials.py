"""Load plan-level denial statistics from the CMS Transparency in Coverage PUF (xlsx).

One sheet row is one plan. A plan is either reported (numbers, some suppressed) or not
reported at all (new to the Exchange). A file that fails any quality gate is rejected
outright: nothing is returned. Validated rows are then upserted into `plan_denial_stats`,
a public reference table (no `account_id`).
"""

import logging
from pathlib import Path
from typing import Any, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)
from sqlalchemy.orm import Session

from src.db.models import PLAN_COUNT_COLUMNS, MetalLevel, PlanDenialStats, PlanType
from src.ingest.marketplace_denials import (
    BatchRejectedError,
    Count,
    describe_validation_error,
    read_sheet_rows,
    upsert_rows,
)

logger = logging.getLogger(__name__)

# What the legend puts in place of a small number it will not publish. Not the same as zero.
SUPPRESSED_MARK = "**"
# Legend tokens that mean the plan has no figures: new to the Exchange, not required,
# or not available.
NOT_REPORTED_TOKENS = frozenset({"N/A", "***", "*"})
MAX_PLAN_IDS_IN_WARNING = 10
PLAN_UPSERT_CONSTRAINT = "uq_plan_denial_stats_plan_year"
PLAN_UPSERT_KEY_COLUMNS = ("plan_id", "plan_year")
REASON_COLUMNS = tuple(name for name in PLAN_COUNT_COLUMNS if name.startswith("denied_"))

# Row model field -> exact header text in the sheet (the typos are in the source).
PLAN_SOURCE_HEADERS = {
    "state": "State",
    "issuer_id": "Issuer_ID",
    "plan_id": "Plan_ID",
    "plan_type": "Plan_Type",
    "metal_level": "Metal_Level",
    "claims_received_out_of_network": "Plan_Number_Claims_Received_Out_of_Network",
    "claims_received_in_network": "Plan_Number_Claims_Received_In_Network",
    "claims_denied_out_of_network": "Plan_Number_Claims_Denied_Out_of_Network",
    "claims_denied_in_network": "Plan_Number_Claims_Denied_In_Network",
    "claims_resubmitted_out_of_network": "Plan_Number_Claims_Resubmitted_Out_of_Network",
    "claims_resubmitted_in_network": "Plan_Number_Claims_Resubmitted_In_Network",
    "denied_referral_required": "Plan_Number_Claims_Denied_Referral_Required",
    "denied_out_of_network": "Plan_Number_Claims_Denied_Due_To_Out_Of_Network",
    "denied_services_excluded": "Plan_Number_Claims_Denied_Services_Excluded",
    "denied_not_medically_necessary_non_bh": (
        "Plan_Number_Claims_Denied_Not_Medically_Necessary_Excluding_Behavioral_Health"
    ),
    "denied_not_medically_necessary_bh": (
        "Plan_Number_Claims_Denied_Not_Medically_Necessary_Behavioral_Health_Only"
    ),
    "denied_benefit_limit_reached": (
        "Plan_Number_Claims_Denied_Due_To_Enrolle_Benefit_Limit_Reached"
    ),
    "denied_member_not_covered": "Plan_Number_Claims_Denied_Due_To_Member_Not_Covered",
    "denied_investigational_experimental_cosmetic": (
        "Plan_Number_Claims_Denied_Due_To_Investigational_Experimental_Cosmetic_Proceduce"
    ),
    "denied_administrative_reason": "Plan_Number_Claims_Denied_Due_To_Administrative_Reason",
    "denied_other": "Plan_Number_Claims_Denied_Other",
}


class PlanDenialRow(BaseModel):
    """One validated plan row, shaped like the `plan_denial_stats` table.

    On a reported plan a count of None means suppressed. A plan that is not reported has
    no counts at all.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    plan_year: int = Field(ge=2000, le=2100)
    plan_id: str = Field(pattern=r"^[0-9]{5}[A-Z]{2}[0-9]{7}$")
    issuer_id: str = Field(pattern=r"^[0-9]{5}$")
    state: str = Field(pattern=r"^[A-Z]{2}$")
    plan_type: PlanType
    metal_level: MetalLevel
    is_reported: bool
    claims_received_out_of_network: Count
    claims_received_in_network: Count
    claims_denied_out_of_network: Count
    claims_denied_in_network: Count
    claims_resubmitted_out_of_network: Count
    claims_resubmitted_in_network: Count
    denied_referral_required: Count
    denied_out_of_network: Count
    denied_services_excluded: Count
    denied_not_medically_necessary_non_bh: Count
    denied_not_medically_necessary_bh: Count
    denied_benefit_limit_reached: Count
    denied_member_not_covered: Count
    denied_investigational_experimental_cosmetic: Count
    denied_administrative_reason: Count
    denied_other: Count

    @field_validator("issuer_id", mode="before")
    @classmethod
    def _issuer_id_as_text(cls, value: Any) -> Any:
        # The sheet stores the id as a number.
        if isinstance(value, int) and not isinstance(value, bool):
            return str(value)
        return value

    @field_validator(*PLAN_COUNT_COLUMNS, mode="before")
    @classmethod
    def _no_booleans(cls, value: Any) -> Any:
        if isinstance(value, bool):
            raise ValueError("must be a number")  # otherwise TRUE would be read as 1
        return value

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if self.plan_id[:5] != self.issuer_id or self.plan_id[5:7] != self.state:
            raise ValueError("plan id does not start with the issuer id and state")
        if not self.is_reported and any(
            getattr(self, name) is not None for name in PLAN_COUNT_COLUMNS
        ):
            raise ValueError("a plan that is not reported cannot have counts")
        return self


def read_plan_denial_rows(path: Path, plan_year: int) -> list[PlanDenialRow]:
    """Read the PUF workbook at `path` and return one validated row per plan.

    Raises `BatchRejectedError` if any quality gate fails (see `read_sheet_rows` for the
    file-level ones). Reading the full file takes a few seconds.
    """
    problems: list[str] = []
    rows: list[PlanDenialRow] = []
    seen: set[str] = set()
    for row_number, raw in read_sheet_rows(path, plan_year, PLAN_SOURCE_HEADERS):
        cells = {name: raw[name] for name in PLAN_COUNT_COLUMNS}
        # The source never leaves a number blank (it uses a legend token), so a blank
        # means a damaged row, not a missing value.
        blank = [name for name, cell in cells.items() if cell is None]
        if blank:
            problems.extend(f"row {row_number}: {name}: blank cell" for name in blank)
            continue
        not_reported = [name for name, cell in cells.items() if cell in NOT_REPORTED_TOKENS]
        if not_reported and len(not_reported) < len(cells):
            problems.append(
                f"row {row_number}: mixes reported values with 'N/A', '***' or '*'; "
                "a plan is either reported or not"
            )
            continue
        counts = {
            name: None if not_reported or cell == SUPPRESSED_MARK else cell
            for name, cell in cells.items()
        }
        candidate = {**raw, **counts, "plan_year": plan_year, "is_reported": not not_reported}
        try:
            row = PlanDenialRow.model_validate(candidate)
        except ValidationError as exc:
            problems.extend(describe_validation_error(row_number, exc))
            continue
        if row.plan_id in seen:
            problems.append(f"row {row_number}: plan id appears more than once in the file")
            continue
        seen.add(row.plan_id)
        rows.append(row)
    if problems:
        raise BatchRejectedError(problems)

    _warn_implausible(rows)
    logger.info("read %d plan rows for plan year %d", len(rows), plan_year)
    return rows


def upsert_plan_denial_rows(session: Session, rows: list[PlanDenialRow]) -> int:
    """Insert `rows` into `plan_denial_stats`, updating any plan + plan year already there.

    The issuer rows of the same plan year must be written first (foreign key). Loading the
    same rows twice changes nothing except `updated_at`. Returns the number of rows written.
    Does not commit: the caller owns the transaction.
    """
    written = upsert_rows(
        session, PlanDenialStats, rows, PLAN_UPSERT_CONSTRAINT, PLAN_UPSERT_KEY_COLUMNS
    )
    logger.info("upserted %d plan rows", written)
    return written


def _above(value: int | None, limit: int | None) -> bool:
    return value is not None and limit is not None and value > limit


def _total_denied(row: PlanDenialRow) -> int | None:
    if row.claims_denied_out_of_network is None or row.claims_denied_in_network is None:
        return None
    return row.claims_denied_out_of_network + row.claims_denied_in_network


def _reasons_below_denied(row: PlanDenialRow) -> bool:
    reasons = [getattr(row, name) for name in REASON_COLUMNS]
    total = _total_denied(row)
    return total is not None and None not in reasons and sum(reasons) < total


def _warn_implausible(rows: list[PlanDenialRow]) -> None:
    # These look wrong but occur in the published file (reporting practice differs between
    # issuers), so they are logged instead of rejecting the batch.
    checks = {
        "more claims denied than received": lambda row: (
            _above(row.claims_denied_out_of_network, row.claims_received_out_of_network)
            or _above(row.claims_denied_in_network, row.claims_received_in_network)
        ),
        "more claims resubmitted than received": lambda row: (
            _above(row.claims_resubmitted_out_of_network, row.claims_received_out_of_network)
            or _above(row.claims_resubmitted_in_network, row.claims_received_in_network)
        ),
        "a denial reason above the total claims denied": lambda row: any(
            _above(getattr(row, name), _total_denied(row)) for name in REASON_COLUMNS
        ),
        "denial reasons that add up to less than the claims denied": _reasons_below_denied,
    }
    for label, is_odd in checks.items():
        odd = [row.plan_id for row in rows if is_odd(row)]
        if odd:
            shown = ", ".join(odd[:MAX_PLAN_IDS_IN_WARNING])
            hidden = len(odd) - MAX_PLAN_IDS_IN_WARNING
            more = f" and {hidden} more" if hidden > 0 else ""
            logger.warning("%d plan(s) report %s: %s%s", len(odd), label, shown, more)
