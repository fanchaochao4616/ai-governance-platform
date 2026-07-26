"""Auth: login / logout / change password / me."""

from fastapi.testclient import TestClient

from app.db import Base, engine, init_db
from app.main import app
from config import DEFAULT_ADMIN_PASSWORD, DEFAULT_ADMIN_USERNAME


def setup_module():
    Base.metadata.drop_all(bind=engine)
    init_db()


client = TestClient(app)


def test_login_me_logout_change_password():
    r = client.post(
        "/api/v1/auth/login",
        json={"username": "wrong", "password": "x"},
    )
    assert r.status_code == 401

    r = client.post(
        "/api/v1/auth/login",
        json={
            "username": DEFAULT_ADMIN_USERNAME,
            "password": DEFAULT_ADMIN_PASSWORD,
        },
    )
    assert r.status_code == 200, r.text
    token = r.json()["token"]
    assert r.json()["user"]["username"] == DEFAULT_ADMIN_USERNAME
    headers = {"Authorization": f"Bearer {token}"}

    r = client.get("/api/v1/auth/me", headers=headers)
    assert r.status_code == 200
    assert r.json()["role"] == "admin"

    r = client.post(
        "/api/v1/auth/change-password",
        headers=headers,
        json={"old_password": "bad", "new_password": "newpass1"},
    )
    assert r.status_code == 400

    r = client.post(
        "/api/v1/auth/change-password",
        headers=headers,
        json={
            "old_password": DEFAULT_ADMIN_PASSWORD,
            "new_password": "newpass1",
        },
    )
    assert r.status_code == 200

    r = client.post(
        "/api/v1/auth/login",
        json={"username": DEFAULT_ADMIN_USERNAME, "password": "newpass1"},
    )
    assert r.status_code == 200
    token2 = r.json()["token"]
    h2 = {"Authorization": f"Bearer {token2}"}

    # restore password for other tests in same module
    r = client.post(
        "/api/v1/auth/change-password",
        headers=h2,
        json={"old_password": "newpass1", "new_password": DEFAULT_ADMIN_PASSWORD},
    )
    assert r.status_code == 200

    r = client.post("/api/v1/auth/logout", headers=h2)
    assert r.status_code == 200
    r = client.get("/api/v1/auth/me", headers=h2)
    assert r.status_code == 401
