"""The overview: how many jobs and drafts the account has, what they cost, and how long they took.

`jobs` and `upload_drafts` are business tables: every query filters on `account_id`. The cost
is the sum of `jobs.cost_usd`, what each job's recorded model calls cost; `llm_calls` (an
operational table without `account_id`) is not read. The month is the database clock's.
No function here commits or calls a model.
"""

from decimal import Decimal

from pydantic import BaseModel, ConfigDict
from sqlalchemy import extract, func, select
from sqlalchemy.orm import Session

from src.db.models import DraftReviewStatus, EndState, Job, JobStatus, UploadDraft


class Overview(BaseModel):
    """Counts with every value present (0 when none), costs in exact dollars.

    `average_seconds` is the mean time from upload to job end over the jobs that started and
    ended; None when there is none. `jobs_by_end_state` counts the `done` jobs only.
    """

    model_config = ConfigDict(frozen=True)

    jobs_by_status: dict[JobStatus, int]
    jobs_by_end_state: dict[EndState, int]
    drafts_by_review_status: dict[DraftReviewStatus, int]
    cost_usd_this_month: Decimal
    cost_usd_total: Decimal
    average_seconds: float | None


def read_overview(session: Session, account_id: int) -> Overview:
    """Return the overview of `account_id`."""
    by_status = dict(
        session.execute(
            select(Job.status, func.count())
            .where(Job.account_id == account_id)
            .group_by(Job.status)
        ).all()
    )
    by_end_state = dict(
        session.execute(
            select(Job.end_state, func.count())
            .where(Job.account_id == account_id, Job.end_state.is_not(None))
            .group_by(Job.end_state)
        ).all()
    )
    by_review = dict(
        session.execute(
            select(UploadDraft.review_status, func.count())
            .where(UploadDraft.account_id == account_id)
            .group_by(UploadDraft.review_status)
        ).all()
    )
    month_start = func.date_trunc("month", func.now())
    total, this_month, average = session.execute(
        select(
            func.coalesce(func.sum(Job.cost_usd), 0),
            func.coalesce(func.sum(Job.cost_usd).filter(Job.created_at >= month_start), 0),
            func.avg(extract("epoch", Job.finished_at - Job.created_at)).filter(
                Job.started_at.is_not(None), Job.finished_at.is_not(None)
            ),
        ).where(Job.account_id == account_id)
    ).one()
    return Overview(
        jobs_by_status={value: by_status.get(value, 0) for value in JobStatus},
        jobs_by_end_state={value: by_end_state.get(value, 0) for value in EndState},
        drafts_by_review_status={value: by_review.get(value, 0) for value in DraftReviewStatus},
        cost_usd_this_month=Decimal(this_month),
        cost_usd_total=Decimal(total),
        average_seconds=None if average is None else float(average),
    )
