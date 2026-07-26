"""Template create / version control tests."""

from fastapi.testclient import TestClient

from app.db import Base, engine, init_db
from app.main import app
from config import DEFAULT_ADMIN_PASSWORD, DEFAULT_ADMIN_USERNAME


def setup_module():
    Base.metadata.drop_all(bind=engine)
    init_db()


client = TestClient(app)


def _auth() -> dict[str, str]:
    r = client.post(
        "/api/v1/auth/login",
        json={
            "username": DEFAULT_ADMIN_USERNAME,
            "password": DEFAULT_ADMIN_PASSWORD,
        },
    )
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


def test_create_save_version_activate_diff():
    headers = _auth()
    r = client.post(
        "/api/v1/templates",
        headers=headers,
        json={
            "name": "demo",
            "category": "risk",
            "description": "test",
            "prompt_text": "rule A\nscore carefully",
            "change_reason": "v1",
            "tags": ["t1"],
        },
    )
    assert r.status_code == 200, r.text
    t = r.json()
    tid = t["id"]
    assert t["current_version"] == 1
    assert t["version_count"] == 1

    r = client.post(
        f"/api/v1/templates/{tid}/versions",
        headers=headers,
        json={
            "prompt_text": "rule A\nscore carefully\nadd edge cases",
            "change_reason": "add edges",
        },
    )
    assert r.status_code == 200, r.text
    assert r.json()["version"] == 2
    assert r.json()["is_active"] is True

    r = client.get(f"/api/v1/templates/{tid}", headers=headers)
    assert r.json()["current_version"] == 2
    assert "edge" in r.json()["prompt_text"]

    r = client.get(f"/api/v1/templates/{tid}/versions", headers=headers)
    assert len(r.json()) == 2

    r = client.get(f"/api/v1/templates/{tid}/versions/2/diff", headers=headers)
    assert r.status_code == 200
    body = r.json()
    assert body.get("diff")
    assert "edge" in body["diff"] or any(
        "edge" in (row.get("text") or "") for row in (body.get("diff_rows") or [])
    )
    assert body.get("has_parent") is True
    assert body.get("parent_version") == 1
    assert isinstance(body.get("diff_rows"), list)
    assert len(body["diff_rows"]) > 0

    r = client.post(
        f"/api/v1/templates/{tid}/versions/1/activate", headers=headers
    )
    assert r.status_code == 200
    assert r.json()["version"] == 1
    assert r.json()["is_active"] is True

    r = client.get(f"/api/v1/templates/{tid}", headers=headers)
    assert r.json()["current_version"] == 1
    assert r.json()["prompt_text"] == "rule A\nscore carefully"

    r = client.post(
        f"/api/v1/templates/{tid}/versions",
        headers=headers,
        json={"prompt_text": "rule A\nscore carefully", "change_reason": "noop"},
    )
    assert r.status_code == 400
