"""The overview: how many jobs and drafts a `JobScope` holds, what they cost, how long they took.

`jobs` and `upload_drafts` are business tables: every query filters on `account_id`. A scope
with an owner counts that user's jobs only, and gets no cost (only admins see what the model
calls cost). The cost
is the sum of `jobs.cost_usd`, what each job's recorded model calls cost; `llm_calls` (an
operational table without `account_id`) is not read. The month is the database clock's.
No function here commits or calls a model.
"""

from decimal import Decimal

from pydantic import BaseModel, ConfigDict
from sqlalchemy import and_, extract, func, select
from sqlalchemy.orm import Session

from src.db.models import DraftReviewStatus, EndState, Job, JobStatus, UploadDraft
from src.jobs.store import JobScope, scope_filter


class Overview(BaseModel):
    """Counts with every value present (0 when none), costs in exact dollars.

    `average_seconds` is the mean time from upload to job end over the jobs that started and
    ended; None when there is none. `jobs_by_end_state` counts the `done` jobs only. The two
    costs are None for a scope with an owner.
    """

    model_config = ConfigDict(frozen=True)

    jobs_by_status: dict[JobStatus, int]
    jobs_by_end_state: dict[EndState, int]
    drafts_by_review_status: dict[DraftReviewStatus, int]
    cost_usd_this_month: Decimal | None
    cost_usd_total: Decimal | None
    average_seconds: float | None


def read_overview(session: Session, scope: JobScope) -> Overview:
    """Return the overview of the jobs in `scope`."""
    in_scope = scope_filter(scope)
    by_status = dict(
        session.execute(
            select(Job.status, func.count()).where(*in_scope).group_by(Job.status)
        ).all()
    )
    by_end_state = dict(
        session.execute(
            select(Job.end_state, func.count())
            .where(*in_scope, Job.end_state.is_not(None))
            .group_by(Job.end_state)
        ).all()
    )
    by_review = dict(
        session.execute(
            select(UploadDraft.review_status, func.count())
            .join(Job, and_(Job.account_id == UploadDraft.account_id, Job.id == UploadDraft.job_id))
            .where(*in_scope)
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
        ).where(*in_scope)
    ).one()
    shows_cost = scope.owner_id is None
    return Overview(
        jobs_by_status={value: by_status.get(value, 0) for value in JobStatus},
        jobs_by_end_state={value: by_end_state.get(value, 0) for value in EndState},
        drafts_by_review_status={value: by_review.get(value, 0) for value in DraftReviewStatus},
        cost_usd_this_month=Decimal(this_month) if shows_cost else None,
        cost_usd_total=Decimal(total) if shows_cost else None,
        average_seconds=None if average is None else float(average),
    )
