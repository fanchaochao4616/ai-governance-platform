"""Inspect which APIs are configured and recent job gold/prompt activity."""
from __future__ import annotations

from app.db import SessionLocal
from app.models import EventLog, Job, PromptVersion
from config import (
    ANNOTATOR_BASE_URL,
    ANNOTATOR_MODEL,
    QC_BASE_URL,
    QC_MODEL,
    get_annotator_api_key,
    get_qc_api_key,
)


def main() -> None:
    qk = get_qc_api_key()
    ak = get_annotator_api_key()
    print("=== LIVE CONFIG ===")
    print(f"QC      base={QC_BASE_URL}  model={QC_MODEL}  key={qk[:8]}...{qk[-4:]}")
    print(f"Annot   base={ANNOTATOR_BASE_URL}  model={ANNOTATOR_MODEL}  key={ak[:12]}")

    db = SessionLocal()
    try:
        jobs = db.query(Job).order_by(Job.id.desc()).limit(3).all()
        print("\n=== RECENT JOBS ===")
        for j in jobs:
            print(f"  job#{j.id} status={j.status} name={j.name!r}")

        if not jobs:
            print("(no jobs)")
            return

        j = jobs[0]
        print(f"\n=== JOB #{j.id} PROMPT VERSIONS ===")
        pvs = (
            db.query(PromptVersion)
            .filter(PromptVersion.job_id == j.id)
            .order_by(PromptVersion.version)
            .all()
        )
        for v in pvs:
            src = (v.improvement_suggestion or {}).get("source")
            print(
                f"  v{v.version} active={v.is_active} source={src!r} "
                f"reason={(v.change_reason or '')[:100]!r} "
                f"prompt_chars={len(v.prompt_text or '')}"
            )
            if v.is_active:
                preview = (v.prompt_text or "")[:400]
                print("  --- active prompt preview ---")
                print(preview)
                print("  --- end preview ---")

        log = (j.progress or {}).get("gold_log") or []
        print(f"\n=== JOB #{j.id} GOLD_LOG (last 10) ===")
        for x in log[-10:]:
            print(
                f"  step={x.get('step')} ver={x.get('version')} "
                f"acc={x.get('accuracy')} msg={(x.get('message') or '')[:120]}"
            )

        print(f"\n=== JOB #{j.id} EVENTS (last 10) ===")
        evs = (
            db.query(EventLog)
            .filter(EventLog.job_id == j.id)
            .order_by(EventLog.id.desc())
            .limit(10)
            .all()
        )
        for e in evs:
            print(f"  {e.event_type} payload={str(e.payload)[:140]}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
