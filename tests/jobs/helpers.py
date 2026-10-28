"""Two made-up accounts with one admin each, and their uploads and jobs, for the job tests.

`job_accounts` commits the accounts and removes them afterwards (their users, uploads, jobs
and results go with them). No real letter and no real model is used.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass

from sqlalchemy import Engine, delete, select
from sqlalchemy.orm import Session, sessionmaker

from src.db.models import (
    Account,
    Job,
    UploadAgentRun,
    UploadDraft,
    UploadExtraction,
    UploadFileType,
    User,
    UserRole,
)
from src.db.session import create_session_factory, session_scope
from src.jobs.store import JobScope, NewUpload, create_upload_job

ACCOUNT_NAMES = ("Made Up Jobs Clinic A", "Made Up Jobs Clinic B")
MADE_UP_HASH = "$argon2id$v=19$m=65536,t=3,p=4$bWFkZXVw$bWFkZXVwaGFzaA"
UPLOAD_TEXT = "Made-up denial letter text about Zebulon Quillfeather."


@dataclass(frozen=True)
class Member:
    """One account and its admin."""

    account_id: int
    user_id: int


def add_member(session: Session, name: str) -> Member:
    account = Account(name=name)
    session.add(account)
    session.flush()
    user = User(
        account_id=account.id,
        email=f"admin-{account.id}@example.com",
        password_hash=MADE_UP_HASH,
        role=UserRole.ADMIN,
    )
    session.add(user)
    session.flush()
    return Member(account_id=account.id, user_id=user.id)


def add_user(session: Session, member: Member, role: UserRole) -> Member:
    """A second user of `member`'s account, with `role`; returned as a `Member` of that account."""
    user = User(
        account_id=member.account_id,
        email=f"{role.value}-{member.account_id}-{member.user_id}@example.com",
        password_hash=MADE_UP_HASH,
        role=role,
    )
    session.add(user)
    session.flush()
    return Member(account_id=member.account_id, user_id=user.id)


def account_scope(account_id: int) -> JobScope:
    """What an admin of `account_id` reads: every job of the account."""
    return JobScope(account_id=account_id, owner_id=None)


def owner_scope(member: Member) -> JobScope:
    """What a non-admin `member` reads: the jobs of their own uploads."""
    return JobScope(account_id=member.account_id, owner_id=member.user_id)


def new_upload(member: Member, text: str = UPLOAD_TEXT) -> NewUpload:
    return NewUpload(
        user_id=member.user_id,
        file_name="letter.txt",
        file_type=UploadFileType.TEXT,
        byte_size=len(text.encode("utf-8")),
        text=text,
    )


def _remove(factory: sessionmaker[Session]) -> None:
    with session_scope(factory) as session:
        session.execute(delete(Account).where(Account.name.in_(ACCOUNT_NAMES)))


@contextmanager
def job_accounts(
    engine: Engine, factory: sessionmaker[Session] | None = None
) -> Iterator[tuple[Member, Member]]:
    """Commit the two made-up accounts; remove them and all their rows afterwards."""
    factory = factory or create_session_factory(engine)
    _remove(factory)  # in case an earlier run was killed before its clean-up
    with session_scope(factory) as session:
        first, second = (add_member(session, name) for name in ACCOUNT_NAMES)
    try:
        yield first, second
    finally:
        _remove(factory)


def queue_job(factory: sessionmaker[Session], member: Member, text: str = UPLOAD_TEXT) -> int:
    with session_scope(factory) as session:
        return create_upload_job(session, member.account_id, new_upload(member, text))


def read_job(factory: sessionmaker[Session], job_id: int) -> Job:
    with session_scope(factory) as session:
        job = session.scalars(select(Job).where(Job.id == job_id)).one()
        session.expunge(job)
        return job


@dataclass(frozen=True)
class JobResults:
    extraction: UploadExtraction | None
    agent_run: UploadAgentRun | None
    draft: UploadDraft | None


def read_results(factory: sessionmaker[Session], job_id: int) -> JobResults:
    with session_scope(factory) as session:
        results = JobResults(
            extraction=session.scalars(
                select(UploadExtraction).where(UploadExtraction.job_id == job_id)
            ).one_or_none(),
            agent_run=session.scalars(
                select(UploadAgentRun).where(UploadAgentRun.job_id == job_id)
            ).one_or_none(),
            draft=session.scalars(
                select(UploadDraft).where(UploadDraft.job_id == job_id)
            ).one_or_none(),
        )
        session.expunge_all()
    return results
