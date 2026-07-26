"""Shared test fixtures — auth against SQLite users table."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.db import Base, engine, init_db
from app.main import app
from config import DEFAULT_ADMIN_PASSWORD, DEFAULT_ADMIN_USERNAME


@pytest.fixture(scope="module")
def client():
    Base.metadata.drop_all(bind=engine)
    init_db()
    return TestClient(app)


@pytest.fixture
def auth_headers(client: TestClient) -> dict[str, str]:
    r = client.post(
        "/api/v1/auth/login",
        json={
            "username": DEFAULT_ADMIN_USERNAME,
            "password": DEFAULT_ADMIN_PASSWORD,
        },
    )
    assert r.status_code == 200, r.text
    token = r.json()["token"]
    return {"Authorization": f"Bearer {token}"}
