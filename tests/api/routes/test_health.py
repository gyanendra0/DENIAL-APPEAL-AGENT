"""`GET /health` answers without a login."""

from fastapi.testclient import TestClient


def test_health_is_200_without_a_login(api_client: TestClient) -> None:
    response = api_client.get("/health")
    assert (response.status_code, response.json()) == (200, {"status": "ok"})
