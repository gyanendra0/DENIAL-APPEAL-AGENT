"""A health check that needs no login: the API answers and its database answers too."""

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Response, status
from pydantic import BaseModel
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from src.api.dependencies import get_session_factory
from src.db.session import check_database

logger = logging.getLogger(__name__)

router = APIRouter(tags=["health"])

# How long the database may take to answer `SELECT 1`, so a stuck database fails the check
# instead of holding it.
DB_CHECK_TIMEOUT_MS = 2000


class Health(BaseModel):
    status: str


@router.get("/health")
def health(
    response: Response,
    factory: Annotated[sessionmaker[Session], Depends(get_session_factory)],
) -> Health:
    """Answer 200 `ok` when the database answers, else 503 `unavailable`.

    The check uses its own new connection, not `DbSession`, so a frozen pooled connection cannot
    hold it and a failed check is answered instead of failing again on the commit. It can take
    the database connect timeout. The error's text is never sent, only its class is logged.
    """
    try:
        check_database(factory, statement_timeout_ms=DB_CHECK_TIMEOUT_MS)
    except SQLAlchemyError as error:
        logger.warning("health: database unavailable (%s)", type(error).__name__)
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return Health(status="unavailable")
    return Health(status="ok")
