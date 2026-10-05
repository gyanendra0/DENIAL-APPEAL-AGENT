"""Plan reader tests. Fixture plan rows 4 to 8 are reported; row 9 (a new issuer) is not."""

import logging
from pathlib import Path

import pytest
from openpyxl.utils import get_column_letter

from src.db.models import PLAN_COUNT_COLUMNS, MetalLevel, PlanType
from src.ingest.marketplace_denials import BatchRejectedError
from src.ingest.marketplace_plan_denials import PlanDenialRow, read_plan_denial_rows
from tests.ingest.helpers import FIXTURE, PLAN_YEAR, CellValue, edited_copy

# The 16 plan count columns are Y to AN (columns 25 to 40).
COUNT_LETTERS = [get_column_letter(index) for index in range(25, 41)]


def _whole_row(row: int, value: CellValue) -> dict[str, CellValue]:
    """Edits that put `value` in every plan count cell of one sheet row."""
    return {f"{letter}{row}": value for letter in COUNT_LETTERS}


def test_returns_one_row_per_plan() -> None:
    rows = read_plan_denial_rows(FIXTURE, PLAN_YEAR)

    assert [row.plan_id for row in rows] == [
        "11111TX0010001",
        "11111TX0010002",
        "11111TX0010003",
        "22222OR0010004",
        "22222OR0010005",
        "33333OH0010006",
    ]


def test_reads_every_field_of_a_reported_plan() -> None:
    first = read_plan_denial_rows(FIXTURE, PLAN_YEAR)[0]

    assert first == PlanDenialRow(
        plan_year=PLAN_YEAR,
        plan_id="11111TX0010001",
        issuer_id="11111",
        state="TX",
        plan_type=PlanType.HMO,
        metal_level=MetalLevel.SILVER,
        is_reported=True,
        claims_received_out_of_network=10,
        claims_received_in_network=100,
        claims_denied_out_of_network=1,
        claims_denied_in_network=2,
        claims_resubmitted_out_of_network=None,  # "**" in the sheet
        claims_resubmitted_in_network=1,
        denied_referral_required=1,
        denied_out_of_network=1,
        denied_services_excluded=1,
        denied_not_medically_necessary_non_bh=1,
        denied_not_medically_necessary_bh=1,
        denied_benefit_limit_reached=1,
        denied_member_not_covered=1,
        denied_investigational_experimental_cosmetic=1,
        denied_administrative_reason=1,
        denied_other=1,
    )


def test_keeps_zero_apart_from_suppressed(tmp_path: Path) -> None:
    path = edited_copy(tmp_path, {"AE4": 0, "AF4": "**"})

    first = read_plan_denial_rows(path, PLAN_YEAR)[0]

    assert first.denied_referral_required == 0
    assert first.denied_out_of_network is None
    assert first.is_reported is True


def test_plan_of_a_new_issuer_is_not_reported() -> None:
    last = read_plan_denial_rows(FIXTURE, PLAN_YEAR)[-1]

    assert last.is_reported is False
    assert all(getattr(last, name) is None for name in PLAN_COUNT_COLUMNS)


def test_new_plan_of_an_existing_issuer_is_not_reported(tmp_path: Path) -> None:
    # Row 5 belongs to issuer 11111, which is not new to the Exchange.
    path = edited_copy(tmp_path, _whole_row(5, "N/A"))

    second = read_plan_denial_rows(path, PLAN_YEAR)[1]

    assert second.is_reported is False
    assert all(getattr(second, name) is None for name in PLAN_COUNT_COLUMNS)


@pytest.mark.parametrize("token", ["***", "*"])
def test_other_no_data_tokens_also_mean_not_reported(tmp_path: Path, token: str) -> None:
    path = edited_copy(tmp_path, _whole_row(5, "N/A") | {"AA5": token, "AE5": token})

    second = read_plan_denial_rows(path, PLAN_YEAR)[1]

    assert second.is_reported is False


@pytest.mark.parametrize(
    ("edits", "expected"),
    [
        ({"H4": "11111TX001"}, "row 4: plan_id"),
        ({"H4": "11111tx0010001"}, "row 4: plan_id"),
        ({"H4": "99999TX0010001"}, "row 4: row: Value error, plan id does not start"),
        ({"H4": "11111OR0010001"}, "row 4: row: Value error, plan id does not start"),
        ({"H5": "11111TX0010001"}, "row 5: plan id appears more than once"),
        ({"I4": "XYZ"}, "row 4: plan_type"),
        ({"K4": "Diamond"}, "row 4: metal_level"),
        ({"E4": 123}, "row 4: issuer_id"),
        ({"Y4": -1}, "row 4: claims_received_out_of_network"),
        ({"Y4": 1.5}, "row 4: claims_received_out_of_network"),
        ({"Y4": "unknown"}, "row 4: claims_received_out_of_network"),
        ({"Y4": True}, "row 4: claims_received_out_of_network: Value error, must be a number"),
        ({"Y4": None}, "row 4: claims_received_out_of_network: blank cell"),
        ({"AN8": None}, "row 8: denied_other: blank cell"),
        ({"Y4": "N/A"}, "row 4: mixes reported values"),
        ({"Y9": 5}, "row 9: mixes reported values"),
        ({"Y9": "**"}, "row 9: mixes reported values"),
        ({"H3": "PlanID"}, "row 3: missing header 'Plan_ID'"),
    ],
)
def test_rejects_a_bad_file(tmp_path: Path, edits: dict[str, CellValue], expected: str) -> None:
    path = edited_copy(tmp_path, edits)

    with pytest.raises(BatchRejectedError, match=expected):
        read_plan_denial_rows(path, PLAN_YEAR)


def test_rejection_lists_every_problem(tmp_path: Path) -> None:
    path = edited_copy(tmp_path, {"I4": "XYZ", "Y7": -1})

    with pytest.raises(BatchRejectedError) as excinfo:
        read_plan_denial_rows(path, PLAN_YEAR)

    assert len(excinfo.value.problems) == 2


def test_rejects_a_plan_year_that_does_not_match_the_file() -> None:
    with pytest.raises(BatchRejectedError, match="sheet 'Transparency 2025 - Ind QHP' not found"):
        read_plan_denial_rows(FIXTURE, 2025)


def test_rejects_a_file_that_is_not_a_workbook(tmp_path: Path) -> None:
    path = tmp_path / "not_really.xlsx"
    path.write_text("just some text")

    with pytest.raises(BatchRejectedError, match="not a readable .xlsx workbook"):
        read_plan_denial_rows(path, PLAN_YEAR)


def test_row_model_rejects_counts_on_a_plan_that_is_not_reported() -> None:
    last = read_plan_denial_rows(FIXTURE, PLAN_YEAR)[-1]

    with pytest.raises(ValueError, match="not reported cannot have counts"):
        PlanDenialRow.model_validate(last.model_dump() | {"denied_other": 0})


def test_clean_fixture_logs_no_warnings(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING):
        read_plan_denial_rows(FIXTURE, PLAN_YEAR)

    assert caplog.records == []


@pytest.mark.parametrize(
    ("edits", "expected"),
    [
        # Row 4 has received 10 / 100, denied 1 / 2, resubmitted ** / 1, and ten reasons of 1.
        ({"AA4": 50}, "1 plan(s) report more claims denied than received"),
        ({"AD4": 500}, "1 plan(s) report more claims resubmitted than received"),
        ({"AN4": 99}, "1 plan(s) report a denial reason above the total claims denied"),
        (
            {"AB4": 90},
            "1 plan(s) report denial reasons that add up to less than the claims denied",
        ),
    ],
)
def test_warns_but_loads_when_numbers_look_implausible(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    edits: dict[str, CellValue],
    expected: str,
) -> None:
    path = edited_copy(tmp_path, edits)

    with caplog.at_level(logging.WARNING):
        rows = read_plan_denial_rows(path, PLAN_YEAR)

    assert len(rows) == 6
    assert expected in caplog.text
