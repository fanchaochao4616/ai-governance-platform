"""数据集清洗：不改写原始 data.jsonl，只记录删除 id，支持 diff 回退。

目录（每个数据集）::

    data/datasets/{id}/
      data.jsonl              # 原始数据（清洗不改写）
      clean/
        state.json            # 当前 deleted 集合 + ops 摘要
        ops/{op_id}.json      # 每批删除/回退 diff

匹配方式::
  keywords | regex | vector_fast | vector | llm | manual（前端勾选）

进度：点「删除选中」即写入 delete diff（含保存进度）。
导出：export-dataset 生成 id_ref 包，仅存生效 id 列表，不复制全文。
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.services import dataset_manage_service as dms
from app.services import dataset_store as store
from app.services import dataset_vector as dvec

CLEAN_DIR = "clean"
OPS_DIR = "ops"
STATE_NAME = "state.json"


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clean_root(root: Path) -> Path:
    return root / CLEAN_DIR


def _ops_dir(root: Path) -> Path:
    return _clean_root(root) / OPS_DIR


def _state_path(root: Path) -> Path:
    return _clean_root(root) / STATE_NAME


def _ensure_clean_dirs(root: Path) -> None:
    _ops_dir(root).mkdir(parents=True, exist_ok=True)


def stable_id(rec: dict[str, Any], index: int) -> str:
    """稳定样本 id：优先业务 id，否则 row:{1-based}。"""
    rid = rec.get("id")
    if rid is not None and str(rid).strip() != "":
        return str(rid)
    return f"row:{index + 1}"


def load_state(root: Path) -> dict[str, Any]:
    path = _state_path(root)
    if not path.exists():
        return {"version": 1, "deleted_ids": [], "ops": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {"version": 1, "deleted_ids": [], "ops": []}
    if not isinstance(data, dict):
        return {"version": 1, "deleted_ids": [], "ops": []}
    data.setdefault("version", 1)
    data.setdefault("deleted_ids", [])
    data.setdefault("ops", [])
    return data


def save_state(root: Path, state: dict[str, Any]) -> None:
    _ensure_clean_dirs(root)
    path = _state_path(root)
    path.write_text(
        json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _load_ops_chrono(root: Path) -> list[dict[str, Any]]:
    """按创建时间正序加载全部 ops。"""
    ops_path = _ops_dir(root)
    items: list[tuple[str, float, dict[str, Any]]] = []
    if not ops_path.exists():
        return []
    for p in ops_path.glob("*.json"):
        try:
            op = json.loads(p.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        if not isinstance(op, dict):
            continue
        created = str(op.get("created_at") or "")
        try:
            mtime = p.stat().st_mtime
        except OSError:
            mtime = 0.0
        items.append((created, mtime, op))
    items.sort(key=lambda x: (x[0], x[1]))
    return [x[2] for x in items]


def _replay_one_op(deleted: set[str], op: dict[str, Any]) -> None:
    """将单条 op 重放到 deleted 集合上（就地修改）。"""
    kind = str(op.get("kind") or "delete").strip().lower()
    if kind in {"checkpoint", "save", "progress"}:
        return
    if kind in {"rollback", "restore"}:
        for i in op.get("restored_ids") or []:
            deleted.discard(str(i))
        return
    # 旧版：打了 restored_at 且无独立 rollback 记录 → 跳过
    if op.get("restored_at") and not op.get("kind"):
        return
    for i in op.get("deleted_ids") or []:
        deleted.add(str(i))


def recompute_deleted_ids(root: Path) -> list[str]:
    """按时间顺序重放全部 ops 得到当前 deleted 集合。"""
    deleted: set[str] = set()
    for op in _load_ops_chrono(root):
        _replay_one_op(deleted, op)
    return sorted(deleted)


def recompute_deleted_ids_until(root: Path, op_id: str) -> list[str]:
    """重放到指定 op（含）为止的 deleted 集合；找不到该 op 则重放全部。"""
    deleted: set[str] = set()
    found = False
    want = str(op_id)
    for op in _load_ops_chrono(root):
        _replay_one_op(deleted, op)
        if str(op.get("id") or "") == want:
            found = True
            break
    if not found:
        return recompute_deleted_ids(root)
    return sorted(deleted)


def _record_snapshot(root: Path, sid: str, original_by_id: dict[str, Any] | None = None) -> dict[str, Any]:
    """按 id 取样本文本快照。"""
    if original_by_id is None:
        original_by_id = {r["_sid"]: r for r in load_original_records(root)}
    r = original_by_id.get(str(sid)) or {}
    return {
        "id": str(sid),
        "seq": r.get("_seq"),
        "text": r.get("text") or "",
        "modality": r.get("modality") or "text",
    }


def sync_state(root: Path) -> dict[str, Any]:
    """从 ops 目录重建 state 摘要（新记录在前）。"""
    deleted_ids = recompute_deleted_ids(root)
    current_deleted = set(deleted_ids)
    summaries: list[dict[str, Any]] = []
    for op in _load_ops_chrono(root):
        kind = str(op.get("kind") or "delete").strip().lower()
        dsum = ""
        if isinstance(op.get("diff"), dict):
            dsum = str(op["diff"].get("summary") or "")
        if kind in {"rollback", "restore"}:
            count = len(op.get("restored_ids") or [])
            can_rollback = False
        elif kind in {"checkpoint", "save", "progress"}:
            snap = op.get("snapshot_deleted_ids")
            if snap is None and isinstance(op.get("diff"), dict):
                snap = op["diff"].get("snapshot_deleted_ids")
            count = len(snap or [])
            can_rollback = False
        else:
            ids = [str(x) for x in (op.get("deleted_ids") or [])]
            count = len(ids)
            # 仅当本批仍有 id 处于当前删除集时才可回退
            can_rollback = any(i in current_deleted for i in ids)
        summaries.append(
            {
                "id": op.get("id"),
                "kind": kind,
                "method": op.get("method"),
                "invert": bool(op.get("invert")),
                "query": op.get("query") or "",
                "count": count,
                "created_at": op.get("created_at"),
                "restored": bool(op.get("restored_at")),
                "restored_at": op.get("restored_at"),
                "label": op.get("label") or "",
                "diff_summary": dsum,
                "has_diff": bool(op.get("diff")),
                "can_rollback": can_rollback,
                "target_op_id": op.get("target_op_id"),
            }
        )
    summaries_new_first = list(reversed(summaries))
    state = {
        "version": 1,
        "deleted_ids": deleted_ids,
        "ops": summaries_new_first,
        "updated_at": _utcnow(),
    }
    save_state(root, state)
    return state


def load_original_records(root: Path) -> list[dict[str, Any]]:
    records = store.load_records(root)
    out: list[dict[str, Any]] = []
    for i, rec in enumerate(records):
        item = dict(rec)
        item["_sid"] = stable_id(rec, i)
        item["_seq"] = i + 1
        out.append(item)
    return out


def active_records(root: Path) -> list[dict[str, Any]]:
    deleted = set(str(x) for x in (load_state(root).get("deleted_ids") or []))
    return [r for r in load_original_records(root) if r["_sid"] not in deleted]


def _parse_keywords(query: str, keywords: list[str] | None) -> list[str]:
    if keywords:
        return [k.strip() for k in keywords if k and str(k).strip()]
    raw = (query or "").strip()
    if not raw:
        return []
    parts = re.split(r"[\s,，|；;]+", raw)
    return [p for p in parts if p]


def _all_as_hits(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """空条件：匹配全部当前生效样本。"""
    return [
        {
            "id": rec["_sid"],
            "seq": rec.get("_seq"),
            "text": rec.get("text"),
            "score": 1.0,
        }
        for rec in records
    ]


def _match_keywords(
    records: list[dict[str, Any]],
    *,
    query: str,
    keywords: list[str] | None,
    match: str,
    case_sensitive: bool,
) -> list[dict[str, Any]]:
    kws = _parse_keywords(query, keywords)
    if not kws:
        return _all_as_hits(records)
    match = (match or "any").lower()
    hits: list[dict[str, Any]] = []
    for rec in records:
        text = str(rec.get("text") or "")
        hay = text if case_sensitive else text.lower()
        found: list[str] = []
        for kw in kws:
            needle = kw if case_sensitive else kw.lower()
            if needle and needle in hay:
                found.append(kw)
        ok = (match == "all" and len(found) == len(kws)) or (
            match != "all" and len(found) > 0
        )
        if ok:
            hits.append(
                {
                    "id": rec["_sid"],
                    "seq": rec.get("_seq"),
                    "text": rec.get("text"),
                    "score": len(found) / max(len(kws), 1),
                    "matched": found,
                }
            )
    return hits


def _match_regex(
    records: list[dict[str, Any]],
    *,
    query: str,
) -> list[dict[str, Any]]:
    pattern = (query or "").strip()
    if not pattern:
        return _all_as_hits(records)
    try:
        cre = re.compile(pattern, re.IGNORECASE)
    except re.error as e:
        raise ValueError(f"正则无效: {e}") from e
    hits: list[dict[str, Any]] = []
    for rec in records:
        text = str(rec.get("text") or "")
        m = cre.search(text)
        if not m:
            continue
        hits.append(
            {
                "id": rec["_sid"],
                "seq": rec.get("_seq"),
                "text": rec.get("text"),
                "score": 1.0,
                "span": m.group(0)[:80],
            }
        )
    return hits


# 大模型清洗：合并为单条 user 提示（不传 system；改格式需同步 _parse_llm_items）
_LLM_CLEAN_PROMPT = """你是严格的文本条件相关性判定器。

【唯一任务】
对照「下方的本次筛选条件」，判断每条候选样本文本和条件的相关性。按输入顺序，输出所有样本的id及对应的相关性分数。
禁止解释、禁止 markdown、禁止输出样本文本、禁止输出条件以外的任何结论。

【本次筛选条件】
{instruction}

【候选样本】每行格式 id<TAB>text：
{candidates}

【输出】#按JSON格式输出，不要解释，score值越高，相关性越高。
{{"items":[{{"id":"样本id","score":0.0到1.0}}]}}

【硬性约束】
1. id必须是候选列表中的原始id字符串
2. 必须覆盖候选列表中的每一个id，低相关也要输出（score可接近0），禁止只输出高相关样本
3. 禁止items以外的顶层字段
4. 必须是合法JSON格式
"""


def _parse_llm_items(raw: str) -> list[dict[str, Any]]:
    """从模型输出解析 [{id, score}, ...]。兼容旧版仅 ids 数组。"""
    import json as _json
    import re as _re

    text = (raw or "").strip()
    if not text:
        return []
    if text.startswith("```"):
        text = _re.sub(r"^```(?:json)?\s*", "", text)
        text = _re.sub(r"\s*```$", "", text)
        text = text.strip()
    data: Any
    try:
        data = _json.loads(text)
    except _json.JSONDecodeError:
        m = _re.search(r"\{[\s\S]*\}|\[[\s\S]*\]", text)
        if not m:
            return []
        try:
            data = _json.loads(m.group(0))
        except _json.JSONDecodeError:
            return []

    items_raw: list[Any] = []
    if isinstance(data, dict):
        if isinstance(data.get("items"), list):
            items_raw = data["items"]
        elif isinstance(data.get("ids"), list):
            # 兼容旧格式 {"ids":[...]}
            items_raw = data["ids"]
        else:
            items_raw = data.get("id_list") or data.get("delete_ids") or []
            if not isinstance(items_raw, list):
                items_raw = []
    elif isinstance(data, list):
        items_raw = data
    else:
        return []

    out: list[dict[str, Any]] = []
    for it in items_raw:
        if isinstance(it, dict):
            sid = it.get("id")
            if sid is None or str(sid).strip() == "":
                continue
            try:
                sc = float(it.get("score", it.get("confidence", 1.0)))
            except (TypeError, ValueError):
                sc = 1.0
            sc = max(0.0, min(1.0, sc))
            out.append({"id": str(sid), "score": sc})
        elif it is not None and str(it).strip() != "":
            out.append({"id": str(it), "score": 1.0})
    return out


def _match_llm(
    records: list[dict[str, Any]],
    *,
    instruction: str,
    batch_size: int = 40,
) -> list[dict[str, Any]]:
    """本地 Annotator/Ollama（Qwen）对全部生效样本打分。

    始终返回与 records 等长的结果：模型未返回的 id 记 score=0.0。
    不做阈值过滤，阈值由前端「应用阈值」本地筛选。
    """
    from config import ANNOTATOR_MODEL
    from llm_client import chat, get_annotator_client

    inst = (instruction or "").strip()
    if not inst:
        raise ValueError("大模型清洗需要清洗提示词（写在清洗条件中）")
    if not records:
        return []

    client = get_annotator_client()
    by_id = {str(r["_sid"]): r for r in records}
    # 预填全量 0 分，保证无关样本也出现在结果列表里
    best: dict[str, float] = {str(r["_sid"]): 0.0 for r in records}
    bs = max(5, min(int(batch_size), 80))

    for start in range(0, len(records), bs):
        chunk = records[start : start + bs]
        lines = []
        for r in chunk:
            tid = str(r["_sid"])
            text = str(r.get("text") or "").replace("\n", " ").strip()
            if len(text) > 400:
                text = text[:400] + "…"
            lines.append(f"{tid}\t{text}")
        # 合并为单条 user 提示，不传 system
        prompt = _LLM_CLEAN_PROMPT.format(
            instruction=inst,
            candidates="\n".join(lines),
        )
        try:
            out = chat(
                [{"role": "user", "content": prompt}],
                model=ANNOTATOR_MODEL,
                temperature=0.0,
                client=client,
            )
        except Exception as e:  # noqa: BLE001
            raise RuntimeError(
                f"本地大模型调用失败（{ANNOTATOR_MODEL}）：{e}"
            ) from e
        for item in _parse_llm_items(out):
            sid = item["id"]
            sc = float(item["score"])
            if sid not in by_id:
                continue
            if sc > best.get(sid, 0.0):
                best[sid] = sc
        # 模型漏掉的 id 保持预填 0.0

    hits: list[dict[str, Any]] = []
    for sid, sc in sorted(best.items(), key=lambda x: -x[1]):
        r = by_id[sid]
        hits.append(
            {
                "id": sid,
                "seq": r.get("_seq"),
                "text": r.get("text"),
                "score": float(sc),
                "llm": True,
            }
        )
    return hits


def _match_vector(
    root: Path,
    records: list[dict[str, Any]],
    *,
    query: str,
    kind: str,
    top_k: int | None = None,
    min_score: float | None = None,
) -> list[dict[str, Any]]:
    """向量清洗匹配：返回生效样本上的全部带分命中，不做阈值/Top-K 截断。

    阈值由前端在返回结果上本地筛选。min_score/top_k 保留参数兼容，清洗路径忽略。
    """
    q = (query or "").strip()
    if not q:
        return _all_as_hits(records)
    active_ids = {r["_sid"] for r in records}
    n_active = max(len(active_ids), 1)
    # 清洗：取足够大的候选再滤 active；不按阈值/Top-K 截断
    fetch_k = min(n_active, 10000)
    try:
        # 后端不按 min_score 过滤，完整返回带分结果
        raw = dvec.search(root, q, top_k=fetch_k, kind=kind, min_score=None)
    except Exception as e:  # noqa: BLE001
        raise ValueError(f"向量匹配失败: {e}") from e

    hits: list[dict[str, Any]] = []
    for h in raw:
        # 对齐 id
        hid = h.get("id")
        sid = str(hid) if hid is not None and str(hid).strip() != "" else None
        if sid is None:
            # 用 seq/text 回落
            seq = h.get("seq")
            if seq is not None:
                sid = f"row:{int(seq)}"
        if sid is None or sid not in active_ids:
            # 再按 text 对齐
            t = str(h.get("text") or "").strip()
            if not t:
                continue
            for r in records:
                if str(r.get("text") or "").strip() == t:
                    sid = r["_sid"]
                    break
        if sid is None or sid not in active_ids:
            continue
        if any(x["id"] == sid for x in hits):
            continue
        hits.append(
            {
                "id": sid,
                "seq": h.get("seq"),
                "text": h.get("text"),
                "score": float(h.get("score") or 0),
                "cosine": h.get("cosine"),
            }
        )
    return hits


def match_records(
    db: Session,
    dataset_id: int,
    *,
    method: str,
    query: str = "",
    keywords: list[str] | None = None,
    match: str = "any",
    case_sensitive: bool = False,
    invert: bool = False,
    top_k: int = 50,
    min_score: float | None = None,
) -> dict[str, Any]:
    """在当前仍生效样本上匹配；可选条件反选。不写入删除。

    query 为空：视为匹配全部生效样本（空条件预览/全量选择）。
    """
    ds = dms.get_dataset(db, int(dataset_id))
    if not ds:
        raise ValueError("dataset not found")
    dms.ensure_file_package(db, ds)
    root = store.dataset_root(ds.id)
    method = (method or "keywords").strip().lower()
    if method in {"keyword", "kw"}:
        method = "keywords"
    if method in {"vector_fast", "tfidf", "fast", "lexical"}:
        method = "vector_fast"
    if method in {"semantic", "bge", "embedding", "vector"}:
        method = "vector"
    if method in {"llm", "llm_local", "qwen", "大模型"}:
        method = "llm"
    if method not in {"keywords", "regex", "vector_fast", "vector", "llm"}:
        raise ValueError(
            "清洗方式仅支持 keywords / regex / vector_fast / vector / llm"
        )

    original = load_original_records(root)
    state = load_state(root)
    deleted_set = set(str(x) for x in (state.get("deleted_ids") or []))
    active = [r for r in original if r["_sid"] not in deleted_set]
    universe_ids = [r["_sid"] for r in active]
    by_id = {r["_sid"]: r for r in active}
    query_empty = not (query or "").strip() and not (
        keywords and any(str(k).strip() for k in keywords)
    )

    # 大模型清洗：返回模型给出的全部 id+score；阈值由前端本地筛选
    if method == "llm":
        if query_empty:
            raise ValueError("大模型清洗请在清洗条件中填写自然语言清洗条件")
        matched = _match_llm(active, instruction=query)
        method_used = "llm"
    elif query_empty:
        matched = _all_as_hits(active)
        method_used = method
    elif method == "keywords":
        matched = _match_keywords(
            active,
            query=query,
            keywords=keywords,
            match=match,
            case_sensitive=case_sensitive,
        )
        method_used = method
    elif method == "regex":
        matched = _match_regex(active, query=query)
        method_used = method
    elif method == "vector_fast":
        st = dvec.index_status(root, "tfidf")
        if not st.get("ready"):
            dvec.build_index(root, kind="tfidf")
        matched = _match_vector(
            root,
            active,
            query=query,
            kind="tfidf",
            top_k=top_k,
            min_score=None,
        )
        method_used = method
    else:
        st = dvec.index_status(root, "model")
        if not st.get("ready"):
            dvec.build_index(root, kind="model")
        matched = _match_vector(
            root,
            active,
            query=query,
            kind="model",
            top_k=top_k,
            min_score=None,
        )
        method_used = method

    matched_ids = [str(h["id"]) for h in matched]
    matched_set = set(matched_ids)

    if invert:
        # 反选：当前生效宇宙中，未匹配到的全部
        selected_ids = [i for i in universe_ids if i not in matched_set]
        selected = []
        for sid in selected_ids:
            r = by_id[sid]
            selected.append(
                {
                    "id": sid,
                    "seq": r.get("_seq"),
                    "text": r.get("text"),
                    "score": 0.0,
                    "inverted": True,
                }
            )
    else:
        selected_ids = matched_ids
        selected = matched

    preview = selected[:50]
    return {
        "dataset_id": ds.id,
        "method": method_used,
        "query": query,
        "query_empty": query_empty,
        "invert": bool(invert),
        "match": match,
        "case_sensitive": case_sensitive,
        "top_k": top_k,
        "min_score": min_score,
        "original_count": len(original),
        "active_count": len(active),
        "already_deleted": len(deleted_set),
        "matched_count": len(matched_ids),
        "selected_count": len(selected_ids),
        "selected_ids": selected_ids,
        "selected": selected,
        "preview": preview,
        "ops": state.get("ops") or [],
    }


def apply_delete(
    db: Session,
    dataset_id: int,
    *,
    method: str,
    query: str = "",
    keywords: list[str] | None = None,
    match: str = "any",
    case_sensitive: bool = False,
    invert: bool = False,
    top_k: int = 50,
    min_score: float | None = None,
    label: str = "",
    selected_ids: list[str] | None = None,
) -> dict[str, Any]:
    """按匹配（可反选）或前端勾选 id 删除：只写入 deleted id 列表。

    selected_ids：前端勾选后的最终删除集合。
    - 传入非空：直接按 id 删除生效样本（支持无条件浏览勾选），并写入 diff 进度
    - 不传：按匹配结果整批删除
    """
    ds = dms.get_dataset(db, int(dataset_id))
    if not ds:
        raise ValueError("dataset not found")
    dms.ensure_file_package(db, ds)
    root = store.dataset_root(ds.id)
    _ensure_clean_dirs(root)

    active_map = {r["_sid"]: r for r in active_records(root)}
    original_count = store.count_records(root)
    before_active = len(active_map)
    match_pool: dict[str, Any] = {}
    matched_count = 0
    method_used = (method or "keywords").strip().lower() or "keywords"
    manual_pick = False

    want_ids: list[str] | None = None
    if selected_ids is not None:
        want_ids = [str(x) for x in selected_ids if str(x).strip() != ""]

    if want_ids is not None and len(want_ids) > 0:
        # 前端勾选优先：无需再跑匹配条件（无条件浏览也可删）
        selected_ids = [i for i in want_ids if i in active_map]
        manual_pick = True
        if not (query or "").strip() and method_used in {
            "keywords",
            "regex",
            "vector",
            "vector_fast",
            "llm",
            "manual",
            "",
        }:
            method_used = "manual"
        matched_count = len(selected_ids)
    else:
        preview = match_records(
            db,
            dataset_id,
            method=method,
            query=query,
            keywords=keywords,
            match=match,
            case_sensitive=case_sensitive,
            invert=invert,
            top_k=top_k,
            min_score=min_score,
        )
        method_used = preview.get("method") or method_used
        match_pool = {
            str(h.get("id")): h
            for h in (preview.get("selected") or [])
            if h.get("id") is not None
        }
        matched_count = int(preview.get("matched_count") or 0)
        before_active = int(preview.get("active_count") or before_active)
        original_count = int(preview.get("original_count") or original_count)
        if want_ids is not None:
            pool = set(match_pool.keys()) if match_pool else set(active_map.keys())
            selected_ids = [
                i for i in want_ids if i in active_map and (i in pool or not match_pool)
            ]
        else:
            selected_ids = list(preview.get("selected_ids") or [])

    if not selected_ids:
        raise ValueError("没有可删除的样本（请勾选行或先预览匹配）")

    # 构建完整 diff：删除前后计数 + 被删样本全文快照
    deleted_records: list[dict[str, Any]] = []
    for sid in selected_ids:
        h = match_pool.get(sid)
        if h:
            deleted_records.append(
                {
                    "id": sid,
                    "seq": h.get("seq"),
                    "text": h.get("text") or "",
                    "score": h.get("score"),
                    "matched": h.get("matched"),
                }
            )
        else:
            r = active_map.get(sid)
            deleted_records.append(
                {
                    "id": sid,
                    "seq": (r or {}).get("_seq"),
                    "text": (r or {}).get("text") or "",
                    "score": None,
                }
            )

    after_active = max(0, before_active - len(selected_ids))
    op_id = uuid.uuid4().hex[:12]
    how = "勾选" if manual_pick else ("反选" if invert else "正选")
    op = {
        "id": op_id,
        "kind": "delete",
        "method": method_used,
        "invert": bool(invert) and not manual_pick,
        "query": query,
        "keywords": keywords,
        "match": match,
        "case_sensitive": case_sensitive,
        "top_k": top_k,
        "min_score": min_score,
        "label": (label or "").strip(),
        "deleted_ids": selected_ids,
        "created_at": _utcnow(),
        "restored_at": None,
        "diff": {
            "action": "delete",
            "before_active": before_active,
            "after_active": after_active,
            "matched_count": matched_count,
            "deleted_count": len(selected_ids),
            "deleted_records": deleted_records,
            "summary": (
                f"{how}删除并保存进度 {len(selected_ids)} 条："
                f"生效 {before_active} → {after_active}"
            ),
        },
    }
    op_path = _ops_dir(root) / f"{op_id}.json"
    op_path.write_text(json.dumps(op, ensure_ascii=False, indent=2), encoding="utf-8")

    state = sync_state(root)
    return {
        "dataset_id": ds.id,
        "op": {
            "id": op_id,
            "method": op["method"],
            "invert": op["invert"],
            "query": op["query"],
            "count": len(selected_ids),
            "created_at": op["created_at"],
            "restored": False,
            "diff_summary": op["diff"]["summary"],
        },
        "deleted_this_op": len(selected_ids),
        "active_count": original_count - len(state.get("deleted_ids") or []),
        "original_count": original_count,
        "already_deleted": len(state.get("deleted_ids") or []),
        "state": state,
        "diff": op["diff"],
        "preview_deleted": deleted_records[:50],
    }


def get_op_diff(db: Session, dataset_id: int, op_id: str) -> dict[str, Any]:
    """读取某次清洗的 diff，并附带「相对当前版本」的差异样本。

    - deleted_records：本批操作涉及的样本（删除/回退快照）
    - vs_current.rows：与当前生效状态不一致的样本（含 status 标注）
    - can_rollback：删除类且本批仍有 id 在当前删除集中
    """
    ds = dms.get_dataset(db, int(dataset_id))
    if not ds:
        raise ValueError("dataset not found")
    root = store.dataset_root(ds.id)
    op_path = _ops_dir(root) / f"{op_id}.json"
    if not op_path.exists():
        raise ValueError("清洗记录不存在")
    op = json.loads(op_path.read_text(encoding="utf-8"))
    kind = str(op.get("kind") or "delete").strip().lower()
    original = load_original_records(root)
    by_id = {r["_sid"]: r for r in original}
    current_deleted = set(recompute_deleted_ids(root))
    then_deleted = set(recompute_deleted_ids_until(root, str(op_id)))

    diff = op.get("diff") if isinstance(op.get("diff"), dict) else None
    if not diff:
        deleted_ids = [str(x) for x in (op.get("deleted_ids") or [])]
        deleted_records = [_record_snapshot(root, sid, by_id) for sid in deleted_ids]
        n = len(deleted_ids)
        diff = {
            "action": "delete",
            "before_active": None,
            "after_active": None,
            "matched_count": None,
            "deleted_count": n,
            "deleted_records": deleted_records,
            "summary": f"删除 {n} 条（旧批次无完整 diff 元数据）",
        }

    # 本批样本 + 相对当前是否仍删除
    batch_rows: list[dict[str, Any]] = []
    raw_batch = list(diff.get("deleted_records") or [])
    if not raw_batch and kind in {"delete", ""}:
        for sid in (op.get("deleted_ids") or []):
            raw_batch.append(_record_snapshot(root, str(sid), by_id))
    if kind in {"rollback", "restore"} and not raw_batch:
        for sid in (op.get("restored_ids") or []):
            raw_batch.append(_record_snapshot(root, str(sid), by_id))
    for r in raw_batch:
        sid = str(r.get("id") or "")
        if not sid:
            continue
        row = dict(r)
        row["id"] = sid
        still = sid in current_deleted
        row["still_deleted"] = still
        row["vs_current"] = "仍已删除" if still else "当前已生效(已回退或不在删除集)"
        batch_rows.append(row)
    diff = dict(diff)
    diff["deleted_records"] = batch_rows

    # 与当前版本集合差异
    only_then = sorted(then_deleted - current_deleted)  # 当时已删、现在生效
    only_now = sorted(current_deleted - then_deleted)  # 当时生效、现在已删
    vs_rows: list[dict[str, Any]] = []
    for sid in only_then:
        rec = _record_snapshot(root, sid, by_id)
        rec["status"] = "当时已删·当前生效"
        rec["vs_current"] = "相对当前：已恢复"
        vs_rows.append(rec)
    for sid in only_now:
        rec = _record_snapshot(root, sid, by_id)
        rec["status"] = "当时生效·当前已删"
        rec["vs_current"] = "相对当前：之后删除"
        vs_rows.append(rec)

    op_deleted_ids = [str(x) for x in (op.get("deleted_ids") or [])]
    can_rollback = kind not in {
        "rollback",
        "restore",
        "checkpoint",
        "save",
        "progress",
    } and any(i in current_deleted for i in op_deleted_ids)

    original_n = len(original)
    return {
        "dataset_id": ds.id,
        "op": {
            "id": op.get("id"),
            "kind": kind,
            "method": op.get("method"),
            "invert": bool(op.get("invert")),
            "query": op.get("query") or "",
            "match": op.get("match"),
            "label": op.get("label") or "",
            "created_at": op.get("created_at"),
            "restored": bool(op.get("restored_at")),
            "restored_at": op.get("restored_at"),
            "count": len(op_deleted_ids)
            if kind not in {"rollback", "restore"}
            else len(op.get("restored_ids") or []),
            "can_rollback": can_rollback,
        },
        "diff": diff,
        "vs_current": {
            "at_op_deleted_count": len(then_deleted),
            "current_deleted_count": len(current_deleted),
            "at_op_active_count": max(0, original_n - len(then_deleted)),
            "current_active_count": max(0, original_n - len(current_deleted)),
            "restored_since_count": len(only_then),
            "deleted_after_count": len(only_now),
            "rows": vs_rows,
            "summary": (
                f"相对当前：当时已删 {len(then_deleted)} / 当前已删 {len(current_deleted)} · "
                f"已恢复 {len(only_then)} · 之后又删 {len(only_now)}"
            ),
        },
        "can_rollback": can_rollback,
    }


def restore_op(db: Session, dataset_id: int, op_id: str) -> dict[str, Any]:
    """回退某次删除类清洗：新增一条「回退」diff 历史（排在第一位），不二次切换。

    - 目标必须是删除类批次（kind=delete）
    - 回退记录 kind=rollback，不可再点回退
    - 原删除记录保持不变，便于查看 diff
    """
    ds = dms.get_dataset(db, int(dataset_id))
    if not ds:
        raise ValueError("dataset not found")
    root = store.dataset_root(ds.id)
    _ensure_clean_dirs(root)
    op_path = _ops_dir(root) / f"{op_id}.json"
    if not op_path.exists():
        raise ValueError("清洗记录不存在")
    op = json.loads(op_path.read_text(encoding="utf-8"))
    kind = str(op.get("kind") or "delete").strip().lower()
    if kind in {"rollback", "restore"}:
        raise ValueError("回退记录不可再次回退")
    if kind in {"checkpoint", "save", "progress"}:
        raise ValueError("进度快照不可回退；请回退对应的删除批次")

    # 待恢复的 id：优先 deleted_ids，兼容仅 diff 快照
    ids = [str(x) for x in (op.get("deleted_ids") or [])]
    if not ids and isinstance(op.get("diff"), dict):
        ids = [
            str(r.get("id"))
            for r in (op["diff"].get("deleted_records") or [])
            if r.get("id") is not None
        ]
    if not ids:
        raise ValueError("该批次无可回退样本")

    before_active = store.count_records(root) - len(recompute_deleted_ids(root))
    after_active = before_active + len(ids)

    # 快照被恢复样本（便于查看此条回退 diff）
    restored_records: list[dict[str, Any]] = []
    src_diff = op.get("diff") if isinstance(op.get("diff"), dict) else {}
    by_id = {
        str(r.get("id")): r
        for r in (src_diff.get("deleted_records") or [])
        if r.get("id") is not None
    }
    original_map = {r["_sid"]: r for r in load_original_records(root)}
    for sid in ids:
        if sid in by_id:
            h = by_id[sid]
            restored_records.append(
                {
                    "id": sid,
                    "seq": h.get("seq"),
                    "text": h.get("text") or "",
                    "score": h.get("score"),
                }
            )
        elif sid in original_map:
            r = original_map[sid]
            restored_records.append(
                {
                    "id": sid,
                    "seq": r.get("_seq"),
                    "text": r.get("text") or "",
                }
            )
        else:
            restored_records.append({"id": sid, "seq": None, "text": ""})

    new_id = uuid.uuid4().hex[:12]
    created = _utcnow()
    new_op = {
        "id": new_id,
        "kind": "rollback",
        "method": "rollback",
        "target_op_id": op_id,
        "query": op.get("query") or "",
        "invert": bool(op.get("invert")),
        "label": op.get("label") or "",
        "restored_ids": ids,
        "deleted_ids": [],
        "created_at": created,
        "restored_at": None,
        "diff": {
            "action": "rollback",
            "target_op_id": op_id,
            "before_active": before_active,
            "after_active": after_active,
            "matched_count": None,
            "deleted_count": 0,
            "restored_count": len(ids),
            "deleted_records": restored_records,
            "summary": (
                f"回退批次 {op_id}：恢复 {len(ids)} 条 · "
                f"生效 {before_active} → {after_active}"
            ),
        },
    }
    new_path = _ops_dir(root) / f"{new_id}.json"
    new_path.write_text(
        json.dumps(new_op, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # 兼容：若旧记录曾写 restored_at，清掉以免双重语义
    if op.get("restored_at"):
        op["restored_at"] = None
        op_path.write_text(
            json.dumps(op, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    state = sync_state(root)
    original_n = store.count_records(root)
    return {
        "dataset_id": ds.id,
        "op_id": new_id,
        "target_op_id": op_id,
        "action": "rollback",
        "restored": True,
        "restored_count": len(ids),
        "reapplied_count": 0,
        "active_count": original_n - len(state.get("deleted_ids") or []),
        "already_deleted": len(state.get("deleted_ids") or []),
        "state": state,
        "diff": new_op["diff"],
    }


def get_clean_overview(db: Session, dataset_id: int) -> dict[str, Any]:
    ds = dms.get_dataset(db, int(dataset_id))
    if not ds:
        raise ValueError("dataset not found")
    dms.ensure_file_package(db, ds)
    root = store.dataset_root(ds.id)
    state = sync_state(root) if _ops_dir(root).exists() else load_state(root)
    if not _state_path(root).exists() and _ops_dir(root).exists():
        state = sync_state(root)
    original = store.count_records(root)
    deleted = len(state.get("deleted_ids") or [])
    active = max(0, original - deleted)
    # 预览当前生效样本
    act = active_records(root)
    preview = [
        {
            "id": r["_sid"],
            "seq": r["_seq"],
            "text": r.get("text"),
            "modality": r.get("modality") or "text",
        }
        for r in act[:50]
    ]
    suggested = default_save_name(ds.name or f"dataset_{ds.id}")
    return {
        "dataset_id": ds.id,
        "name": ds.name,
        "original_count": original,
        "active_count": active,
        "deleted_count": deleted,
        "ops": state.get("ops") or [],
        "preview": preview,
        "suggested_save_name": suggested,
        "note": (
            "原始 data.jsonl 不改写；删除选中=删 id+写 diff；"
            "导出到库=id_ref 仅存生效 id"
        ),
    }


def list_effective_records(
    db: Session, dataset_id: int, *, limit: int = 50, offset: int = 0
) -> dict[str, Any]:
    ds = dms.get_dataset(db, int(dataset_id))
    if not ds:
        raise ValueError("dataset not found")
    dms.ensure_file_package(db, ds)
    root = store.dataset_root(ds.id)
    act = active_records(root)
    total = len(act)
    offset = max(0, int(offset))
    limit = max(1, min(int(limit), 500))
    page = act[offset : offset + limit]
    return {
        "dataset_id": ds.id,
        "total": total,
        "offset": offset,
        "limit": limit,
        "items": [
            {
                "id": r["_sid"],
                "seq": r["_seq"],
                "text": r.get("text"),
                "modality": r.get("modality") or "text",
                "uri": r.get("uri"),
            }
            for r in page
        ],
    }


def export_effective_csv(db: Session, dataset_id: int) -> tuple[Path, str, str]:
    """导出当前生效样本（原始 − 未恢复删除），不改写原包。"""
    from config import EXPORT_DIR, ensure_data_dirs
    import csv

    ds = dms.get_dataset(db, int(dataset_id))
    if not ds:
        raise ValueError("dataset not found")
    root = store.dataset_root(ds.id)
    act = active_records(root)
    ensure_data_dirs()
    out = EXPORT_DIR / f"cleaned_ds{ds.id}_{uuid.uuid4().hex[:8]}.csv"
    with out.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["id", "text", "modality"])
        w.writeheader()
        for r in act:
            w.writerow(
                {
                    "id": r["_sid"],
                    "text": r.get("text") or "",
                    "modality": r.get("modality") or "text",
                }
            )
    return out, "text/csv; charset=utf-8", out.name


def default_save_name(source_name: str, when: datetime | None = None) -> str:
    """默认保存名：原始数据名 + 清洗 + 清洗时间。"""
    base = (source_name or "数据集").strip() or "数据集"
    # 去掉过长后缀，避免名称爆炸
    if len(base) > 80:
        base = base[:80].rstrip()
    t = when or datetime.now()
    # 本地时间字符串：20260726_184530
    stamp = t.strftime("%Y%m%d_%H%M%S")
    return f"{base}_清洗_{stamp}"


def save_effective_as_dataset(
    db: Session,
    dataset_id: int,
    *,
    name: str,
    description: str | None = None,
    build_vectors: bool = True,
) -> dict[str, Any]:
    """将当前生效样本导出为新数据集：**只存 id 引用**，不复制源 data.jsonl 全文。

    包结构::
        data/datasets/{new_id}/
          manifest.json      # kind=id_ref, source_dataset_id
          include_ids.json   # 生效样本 id 列表
          vectors/           # 可选索引（建索引时按需从源解析正文）
    """
    from app.models import ManagedDataset

    src = dms.get_dataset(db, int(dataset_id))
    if not src:
        raise ValueError("dataset not found")
    dms.ensure_file_package(db, src)
    root = store.dataset_root(src.id)
    act = active_records(root)
    if not act:
        raise ValueError("当前生效样本为空，无法导出")

    include_ids = [str(r["_sid"]) for r in act if r.get("_sid") is not None]
    if not include_ids:
        raise ValueError("当前生效样本为空，无法导出")

    new_name = (name or "").strip()
    if not new_name:
        new_name = default_save_name(src.name or f"dataset_{src.id}")

    src_n = store.count_records(root)
    desc = (description or "").strip() or (
        f"由数据集「{src.name}」#{src.id} 清洗后导出（仅 id 引用，不复制全文）；"
        f"生效 {len(include_ids)} 条 / 原始 {src_n} 条"
    )

    ds = ManagedDataset(
        name=new_name,
        description=desc,
        modality=src.modality or "text",
        status="empty",
        original_filename=f"from_clean_ds{src.id}_id_ref",
        file_format="id_ref",
        storage_backend="files",
        source_id_column=src.source_id_column or "id",
        source_text_column=src.source_text_column or "text",
    )
    db.add(ds)
    db.flush()
    new_id = int(ds.id)
    new_root = store.dataset_root(new_id)

    try:
        store.ensure_package_dirs(new_root)
        store.write_include_ids(new_root, include_ids)
        cols = ["id", "text"]
        manifest = {
            "dataset_id": new_id,
            "name": new_name,
            "kind": store.KIND_ID_REF,
            "modality": ds.modality,
            "format": "id_ref",
            "train_hint": (
                "本包仅存储 include_ids.json；读取时按 id 从 source_dataset 解析正文。"
            ),
            "row_count": len(include_ids),
            "source_dataset_id": int(src.id),
            "source_id_column": ds.source_id_column,
            "source_text_column": ds.source_text_column,
            "original_filename": ds.original_filename,
            "include_ids_file": store.INCLUDE_IDS_NAME,
            "cleaned_from": {
                "dataset_id": src.id,
                "name": src.name,
                "saved_at": _utcnow(),
                "effective_count": len(include_ids),
                "source_original_count": src_n,
                "storage": "id_ref_only",
            },
            "files": {
                "include_ids": store.INCLUDE_IDS_NAME,
                "media": store.MEDIA_DIR,
                "vectors": store.VECTORS_DIR,
            },
        }
        store.write_manifest(new_root, manifest)
        ds.root_path = str(new_root)
        ds.storage_backend = "files"
        ds.row_count = len(include_ids)
        ds.column_count = len(cols)
        ds.columns = cols
        ds.id_column = "id"
        ds.text_column = "text"
        ds.status = "ready"
        ds.error_message = None

        if build_vectors:
            ds.status = "indexing"
            db.commit()
            try:
                from app.services import dataset_vector as dvec

                # load_records 会按 id 从源解析正文，索引写在新包 vectors/ 下
                cfg = dvec.build_index(new_root, kind="model")
                ds.vector_ready = True
                ds.vector_model = str(cfg.get("model") or "")
                ds.vector_dim = int(cfg.get("dim") or 0) if cfg.get("dim") else None
                ds.vector_count = int(cfg.get("n") or 0)
            except Exception as ve:  # noqa: BLE001
                ds.vector_ready = False
                ds.error_message = f"语义索引未就绪: {ve}"
            ds.status = "ready"
        else:
            ds.vector_ready = False

        db.commit()
        db.refresh(ds)
        return {
            "source_dataset_id": src.id,
            "source_name": src.name,
            "dataset_id": ds.id,
            "name": ds.name,
            "row_count": int(ds.row_count or 0),
            "vector_ready": bool(ds.vector_ready),
            "description": ds.description,
            "default_name_used": new_name,
            "storage": "id_ref",
            "note": "仅保存生效样本 id 列表，正文从源数据集按需解析",
        }
    except Exception:
        db.rollback()
        try:
            store.delete_package(new_id)
        except Exception:  # noqa: BLE001
            pass
        try:
            orphan = db.get(ManagedDataset, new_id)
            if orphan is not None:
                db.delete(orphan)
                db.commit()
        except Exception:  # noqa: BLE001
            pass
        raise
