"""A liveness check that needs no login and touches no database."""

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter(tags=["health"])


class Health(BaseModel):
    status: str


@router.get("/health")
def health() -> Health:
    return Health(status="ok")
