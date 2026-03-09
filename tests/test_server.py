"""Tests for the FastAPI server module."""

from __future__ import annotations

import http

from fastapi.testclient import TestClient

from claw_plaid_ledger.server import app

client = TestClient(app)


def test_health_returns_200() -> None:
    """`GET /health` responds with HTTP 200."""
    response = client.get("/health")
    assert response.status_code == http.HTTPStatus.OK


def test_health_returns_ok_payload() -> None:
    """`GET /health` body contains status ok."""
    response = client.get("/health")
    assert response.json() == {"status": "ok"}
