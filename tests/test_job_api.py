"""API tests without calling real LLM."""

from pathlib import Path

import pandas as pd
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


def test_health():
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_jobs_require_auth():
    r = client.get("/api/v1/jobs")
    assert r.status_code == 401


def test_create_job_upload_export(tmp_path: Path):
    headers = _auth()
    r = client.post(
        "/api/v1/jobs",
        headers=headers,
        json={
            "name": "t1",
            "policy_rules": "测试细则：命中敏感策略则 label=1",
            "target_accuracy": 1.0,
            "max_gold_iterations": 2,
        },
    )
    assert r.status_code == 200, r.text
    job = r.json()
    job_id = job["id"]
    assert job.get("threshold_set") is False
    assert job["label_schema"]["mode"] == "confidence_threshold"
    assert job["label_schema"].get("threshold_set") is False

    ds = tmp_path / "ds.csv"
    ds.write_text("text,id\n内容A,a1\n内容B,b1\n内容C,c1\n", encoding="utf-8")
    with ds.open("rb") as f:
        r = client.post(
            f"/api/v1/jobs/{job_id}/dataset",
            headers=headers,
            files={"file": ("ds.csv", f, "text/csv")},
        )
    assert r.status_code == 200, r.text
    assert r.json()["count"] == 3

    gold = tmp_path / "gold.xlsx"
    pd.DataFrame({"text": ["内容A", "好内容"], "label": ["1", "0"]}).to_excel(
        gold, index=False
    )
    with gold.open("rb") as f:
        r = client.post(
            f"/api/v1/jobs/{job_id}/gold",
            headers=headers,
            files={
                "file": (
                    "gold.xlsx",
                    f,
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )
            },
        )
    assert r.status_code == 200, r.text
    assert r.json()["count"] == 2

    r = client.get(f"/api/v1/jobs/{job_id}", headers=headers)
    assert r.json()["annotation_count"] == 3
    assert r.json()["gold_count"] == 2

    # Gold 参数：CREATED 可改；模拟 loop 中禁止
    r = client.patch(
        f"/api/v1/jobs/{job_id}/gold-params",
        headers=headers,
        json={"target_accuracy": 0.9, "max_gold_iterations": 5},
    )
    assert r.status_code == 200, r.text
    assert r.json()["target_accuracy"] == 0.9
    assert r.json()["max_gold_iterations"] == 5
    r = client.patch(
        f"/api/v1/jobs/{job_id}/gold-params",
        headers=headers,
        json={"target_accuracy": 1.0},
    )
    assert r.status_code == 200, r.text
    assert r.json()["target_accuracy"] == 1.0
    assert r.json()["max_gold_iterations"] == 5

    from app.db import SessionLocal as _SL
    from app.models import Job as _Job

    _db = _SL()
    try:
        _j = _db.get(_Job, job_id)
        _j.status = "GOLD_OPTIMIZING"
        _db.commit()
    finally:
        _db.close()
    r = client.patch(
        f"/api/v1/jobs/{job_id}/gold-params",
        headers=headers,
        json={"max_gold_iterations": 8},
    )
    assert r.status_code == 400, r.text
    _db = _SL()
    try:
        _j = _db.get(_Job, job_id)
        _j.status = "CREATED"
        _db.commit()
    finally:
        _db.close()

    from app.db import SessionLocal
    from app.models import AnnotationRecord

    db = SessionLocal()
    try:
        for rec in db.query(AnnotationRecord).filter(AnnotationRecord.job_id == job_id):
            rec.rounds = [
                {
                    "round": 1,
                    "label": "正常",
                    "confidence": 0.9,
                    "reasoning": "test",
                    "prompt_version_id": None,
                    "model": "test",
                }
            ]
            rec.current_label = "正常"
            rec.current_confidence = 0.9
            rec.final_label = "正常"
        db.commit()
    finally:
        db.close()

    r = client.get(f"/api/v1/jobs/{job_id}/export?format=xlsx", headers=headers)
    assert r.status_code == 200
    assert (
        "spreadsheetml" in r.headers.get("content-type", "")
        or r.content[:2] == b"PK"
    )

    r = client.get(f"/api/v1/jobs/{job_id}/export?format=csv", headers=headers)
    assert r.status_code == 200
    assert r.content[:2] == b"PK"
