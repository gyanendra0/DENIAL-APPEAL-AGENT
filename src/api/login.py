"""Login: find the user by email and check the password."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.api.passwords import verify_dummy_password, verify_password
from src.db.models import User


def authenticate(session: Session, *, email: str, password: str) -> User | None:
    """Return the user with `email` if `password` is theirs, else None.

    `email` must already be trimmed and lowercased. An unknown email is still checked against
    the dummy hash, so it takes about as long as a wrong password. Takes tens of milliseconds
    on purpose.
    """
    user = find_user_by_email(session, email)
    if user is None:
        verify_dummy_password(password)
        return None
    if not verify_password(user.password_hash, password):
        return None
    return user


def find_user_by_email(session: Session, email: str) -> User | None:
    """Return the user with `email` (already trimmed and lowercased), or None.

    Only for login and the password reset request, where the account is not known yet.
    """
    # The only query on `users` without an `account_id` filter (06-conventions 6.4). It is safe
    # because an email belongs to exactly one account (`uq_users_email`), and neither caller
    # returns anything to the client unless the password matches (login) or at all (the reset
    # request always gives the same answer).
    return session.scalars(select(User).where(User.email == email)).one_or_none()
