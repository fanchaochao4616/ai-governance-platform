"""Dataset cleaning pipeline: load tabular text data, apply methods, export.

支持：
- 上传文件 / 标注 Job / 托管数据集（数据集管理）
- 规则清洗
- 检索筛选（复用数据检索：keywords / regex / vector_fast / vector）
- 本地大模型清洗（Annotator / Ollama）
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd

from app.services.io_tabular import TEXT_ALIASES, read_tabular
from config import (
    ANNOTATOR_BASE_URL,
    ANNOTATOR_MODEL,
    EXPORT_DIR,
    UPLOAD_DIR,
    ensure_data_dirs,
)

# 内存会话（MVP 单机）；进程重启后失效
_SESSIONS: dict[str, "CleanSession"] = {}

_URL_RE = re.compile(r"https?://\S+|www\.\S+", re.I)
_HTML_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
_LLM_DROP_MARKERS = ("DROP", "删除", "丢弃", "无效", "NONE", "NULL")


@dataclass
class CleanSession:
    session_id: str
    source_path: Path
    source_name: str
    df: pd.DataFrame
    text_col: str
    cleaned_df: pd.DataFrame | None = None
    last_report: dict[str, Any] = field(default_factory=dict)
    dataset_id: int | None = None


def list_methods() -> list[dict[str, Any]]:
    """清洗方法目录（前端勾选）。"""
    return [
        {
            "id": "strip_whitespace",
            "name": "去首尾空白",
            "desc": "对文本列 trim 首尾空格/换行",
            "params": [],
        },
        {
            "id": "normalize_whitespace",
            "name": "规范空白",
            "desc": "将连续空白/换行压缩为单个空格",
            "params": [],
        },
        {
            "id": "drop_empty",
            "name": "删除空文本",
            "desc": "去掉文本为空或仅空白的行",
            "params": [],
        },
        {
            "id": "dedupe_exact",
            "name": "精确去重",
            "desc": "按文本列完全相同去重（保留首条）",
            "params": [],
        },
        {
            "id": "dedupe_normalized",
            "name": "规范化去重",
            "desc": "strip + 压缩空白后再按文本去重",
            "params": [],
        },
        {
            "id": "min_length",
            "name": "最短长度过滤",
            "desc": "删除文本长度小于阈值的行",
            "params": [
                {
                    "key": "min_len",
                    "label": "最短字符数",
                    "type": "number",
                    "default": 2,
                    "min": 0,
                    "max": 10000,
                }
            ],
        },
        {
            "id": "max_length",
            "name": "最长长度过滤",
            "desc": "删除文本长度大于阈值的行",
            "params": [
                {
                    "key": "max_len",
                    "label": "最长字符数",
                    "type": "number",
                    "default": 5000,
                    "min": 1,
                    "max": 1000000,
                }
            ],
        },
        {
            "id": "remove_urls",
            "name": "移除 URL",
            "desc": "从文本中删除 http(s)/www 链接",
            "params": [],
        },
        {
            "id": "remove_html",
            "name": "移除 HTML 标签",
            "desc": "去掉尖括号标签，保留纯文本",
            "params": [],
        },
        {
            "id": "to_lower",
            "name": "转小写",
            "desc": "英文等字母转为小写（中文不受影响）",
            "params": [],
        },
        {
            "id": "drop_null_rows",
            "name": "删除全空行",
            "desc": "删除所有列均为空的行",
            "params": [],
        },
        {
            "id": "llm_local",
            "name": "本地大模型清洗",
            "desc": f"使用本机模型（{ANNOTATOR_MODEL} @ {ANNOTATOR_BASE_URL}）按指令改写或过滤文本",
            "params": [
                {
                    "key": "instruction",
                    "label": "清洗指令",
                    "type": "text",
                    "default": (
                        "请清洗文本：规范口语、去除无意义噪声与广告；"
                        "保持原意，只输出清洗后的正文。"
                    ),
                },
                {
                    "key": "action",
                    "label": "模式",
                    "type": "select",
                    "default": "rewrite",
                    "options": [
                        {"value": "rewrite", "label": "改写文本"},
                        {"value": "filter", "label": "判定保留/删除"},
                    ],
                },
                {
                    "key": "max_items",
                    "label": "最多处理条数",
                    "type": "number",
                    "default": 50,
                    "min": 1,
                    "max": 500,
                },
            ],
        },
    ]


def local_llm_info() -> dict[str, Any]:
    """本机大模型配置信息（前端展示）。"""
    return {
        "base_url": ANNOTATOR_BASE_URL,
        "model": ANNOTATOR_MODEL,
        "provider": "annotator",
        "label": f"{ANNOTATOR_MODEL}（本地 Annotator/Ollama）",
    }


def _detect_text_col(df: pd.DataFrame) -> str:
    for col in df.columns:
        key = str(col).strip()
        low = key.lower()
        if low in TEXT_ALIASES or key in TEXT_ALIASES:
            return col
    # 回退：第一列
    if len(df.columns) == 0:
        raise ValueError("文件无任何列")
    return str(df.columns[0])


def _preview_df(df: pd.DataFrame, n: int = 20) -> list[dict[str, Any]]:
    if df is None or df.empty:
        return []
    head = df.head(n).copy()
    # 保证 JSON 可序列化
    records: list[dict[str, Any]] = []
    for _, row in head.iterrows():
        item: dict[str, Any] = {}
        for c in head.columns:
            v = row[c]
            if pd.isna(v):
                item[str(c)] = None
            else:
                item[str(c)] = v if isinstance(v, (int, float, bool)) else str(v)
        records.append(item)
    return records


def _stats(df: pd.DataFrame, text_col: str) -> dict[str, Any]:
    if df is None or df.empty:
        return {"rows": 0, "cols": 0, "empty_text": 0, "unique_text": 0}
    s = df[text_col].astype(str).fillna("").map(lambda x: x if x != "nan" else "")
    empty = int((s.str.strip() == "").sum())
    return {
        "rows": int(len(df)),
        "cols": int(len(df.columns)),
        "empty_text": empty,
        "unique_text": int(s.nunique()),
        "columns": [str(c) for c in df.columns],
    }


def create_session_from_path(
    path: Path,
    *,
    source_name: str | None = None,
    dataset_id: int | None = None,
    df: pd.DataFrame | None = None,
) -> dict[str, Any]:
    ensure_data_dirs()
    path = Path(path) if path else Path(".")
    if df is None:
        if not path.exists():
            raise ValueError("文件不存在")
        df = read_tabular(path)
    if df is None or df.empty:
        raise ValueError("文件为空")
    # 统一列名为 str
    df = df.copy()
    df.columns = [str(c) for c in df.columns]
    text_col = _detect_text_col(df)
    sid = uuid.uuid4().hex
    sess = CleanSession(
        session_id=sid,
        source_path=path,
        source_name=source_name or path.name,
        df=df,
        text_col=text_col,
        dataset_id=int(dataset_id) if dataset_id is not None else None,
    )
    _SESSIONS[sid] = sess
    return {
        "session_id": sid,
        "source_name": sess.source_name,
        "text_col": text_col,
        "dataset_id": sess.dataset_id,
        "stats": _stats(df, text_col),
        "preview": _preview_df(df),
        "methods": list_methods(),
        "llm": local_llm_info(),
    }


def create_session_from_upload(
    upload_path: Path, original_name: str
) -> dict[str, Any]:
    return create_session_from_path(upload_path, source_name=original_name)


def create_session_from_job(db, job_id: int) -> dict[str, Any]:
    """从标注 Job 的 AnnotationRecord 导出为临时表再清洗。"""
    from app.models import AnnotationRecord, Job

    job = db.get(Job, job_id)
    if not job:
        raise ValueError("job not found")
    rows = (
        db.query(AnnotationRecord)
        .filter(AnnotationRecord.job_id == job_id)
        .order_by(AnnotationRecord.seq.asc())
        .all()
    )
    if not rows:
        raise ValueError("该 Job 尚无数据集样本")
    data = {
        "seq": [r.seq for r in rows],
        "text": [r.text or "" for r in rows],
        "external_id": [r.external_id for r in rows],
        "current_label": [r.current_label for r in rows],
        "final_label": [r.final_label for r in rows],
    }
    df = pd.DataFrame(data)
    ensure_data_dirs()
    tmp = UPLOAD_DIR / f"clean_job_{job_id}_{uuid.uuid4().hex[:8]}.csv"
    df.to_csv(tmp, index=False, encoding="utf-8-sig")
    return create_session_from_path(
        tmp, source_name=f"job_{job_id}_{(job.name or 'dataset')}.csv"
    )


def create_session_from_dataset(db, dataset_id: int) -> dict[str, Any]:
    """从托管数据集（数据集管理）载入清洗会话。"""
    from app.services import dataset_manage_service as dms
    from app.services import dataset_store as store

    ds = dms.get_dataset(db, int(dataset_id))
    if not ds:
        raise ValueError("dataset not found")
    dms.ensure_file_package(db, ds)
    root = store.dataset_root(ds.id)
    records = store.load_records(root)
    if not records:
        raise ValueError("数据集为空，无法清洗")
    # 标准化为表格：id / text / modality / uri
    rows: list[dict[str, Any]] = []
    for i, rec in enumerate(records):
        rows.append(
            {
                "id": rec.get("id") if rec.get("id") is not None else i + 1,
                "text": rec.get("text") or "",
                "modality": rec.get("modality") or "text",
                "uri": rec.get("uri"),
            }
        )
    df = pd.DataFrame(rows)
    ensure_data_dirs()
    tmp = UPLOAD_DIR / f"clean_ds_{ds.id}_{uuid.uuid4().hex[:8]}.csv"
    df.to_csv(tmp, index=False, encoding="utf-8-sig")
    return create_session_from_path(
        tmp,
        source_name=ds.name or f"dataset_{ds.id}",
        dataset_id=int(ds.id),
        df=df,
    )


def get_session(session_id: str) -> CleanSession:
    sess = _SESSIONS.get(session_id)
    if not sess:
        raise ValueError("会话不存在或已过期，请重新上传数据集")
    return sess


def _apply_search_filter(
    db,
    sess: CleanSession,
    df: pd.DataFrame,
    filter_spec: dict[str, Any] | None,
) -> tuple[pd.DataFrame, dict[str, Any] | None]:
    """可选：用数据检索能力筛选待清洗子集。query 为空则不过滤。"""
    if not filter_spec:
        return df, None
    query = str(filter_spec.get("query") or "").strip()
    if not query:
        return df, None
    if not sess.dataset_id:
        raise ValueError("检索筛选仅支持托管数据集来源，请从数据集管理载入")

    from app.services import dataset_manage_service as dms

    ds = dms.get_dataset(db, int(sess.dataset_id))
    if not ds:
        raise ValueError("dataset not found")

    mode = str(filter_spec.get("mode") or "keywords").strip().lower()
    top_k = int(filter_spec.get("top_k") or 50)
    limit = int(filter_spec.get("limit") or 500)
    min_score = filter_spec.get("min_score", None)
    match = str(filter_spec.get("match") or "any")
    case_sensitive = bool(filter_spec.get("case_sensitive") or False)

    out = dms.search_dataset_advanced(
        db,
        ds,
        mode=mode,
        query=query,
        keywords=filter_spec.get("keywords"),
        match=match,
        case_sensitive=case_sensitive,
        top_k=top_k,
        limit=limit,
        min_score=min_score,
    )
    hits = out.get("hits") or []
    if not hits:
        empty = df.iloc[0:0].copy()
        return empty, {
            "method": "search_filter",
            "mode": out.get("mode") or mode,
            "query": query,
            "rows_before": len(df),
            "rows_after": 0,
            "removed": len(df),
            "hit_count": 0,
        }

    # 优先按 id 对齐，否则按 text
    hit_ids: set[str] = set()
    hit_texts: set[str] = set()
    ordered_ids: list[str] = []
    for h in hits:
        hid = h.get("id")
        if hid is not None and str(hid) != "":
            sid = str(hid)
            if sid not in hit_ids:
                hit_ids.add(sid)
                ordered_ids.append(sid)
        t = str(h.get("text") or "").strip()
        if t:
            hit_texts.add(t)

    text_col = sess.text_col
    before = len(df)
    if "id" in df.columns and hit_ids:
        id_str = df["id"].map(lambda x: "" if pd.isna(x) else str(x))
        mask = id_str.isin(hit_ids)
        filtered = df.loc[mask].copy()
        # 尽量按命中顺序排列
        order_map = {sid: i for i, sid in enumerate(ordered_ids)}
        filtered_ids = filtered["id"].map(lambda x: "" if pd.isna(x) else str(x))
        filtered = filtered.assign(
            _ord=filtered_ids.map(lambda x: order_map.get(x, 10**9))
        )
        filtered = filtered.sort_values("_ord").drop(columns=["_ord"]).reset_index(
            drop=True
        )
    else:
        texts = df[text_col].astype(str).map(
            lambda x: "" if x == "nan" else str(x).strip()
        )
        filtered = df.loc[texts.isin(hit_texts)].reset_index(drop=True)

    return filtered, {
        "method": "search_filter",
        "mode": out.get("mode") or mode,
        "query": query,
        "rows_before": before,
        "rows_after": len(filtered),
        "removed": before - len(filtered),
        "hit_count": len(hits),
        "min_score": min_score,
        "top_k": top_k,
    }


def _llm_clean_one(text: str, *, instruction: str, action: str) -> str | None:
    """调用本地 Annotator/Ollama。filter 模式返回 None 表示删除。"""
    from llm_client import chat, get_annotator_client
    from config import ANNOTATOR_MODEL

    raw = (text or "").strip()
    if not raw:
        return None if action == "filter" else ""

    if action == "filter":
        system = (
            "你是数据清洗助手。根据用户指令判断样本是否应保留。"
            "只输出 KEEP 或 DROP（大写英文），不要输出其它内容。"
        )
        user = f"清洗指令：{instruction}\n\n样本：\n{raw}\n\n请输出 KEEP 或 DROP："
    else:
        system = (
            "你是数据清洗助手。按用户指令清洗文本。"
            "只输出清洗后的正文，不要解释、不要引号、不要前后缀。"
            "若样本完全无效应删除，只输出 DROP。"
        )
        user = f"清洗指令：{instruction}\n\n原文：\n{raw}\n\n清洗后："

    try:
        client = get_annotator_client()
        out = chat(
            [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            model=ANNOTATOR_MODEL,
            temperature=0.1,
            client=client,
        )
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(f"本地大模型调用失败: {e}") from e

    cleaned = (out or "").strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:\w+)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
        cleaned = cleaned.strip()

    upper = cleaned.upper()
    if action == "filter":
        if upper.startswith("KEEP") or cleaned.startswith("保留"):
            return raw
        return None
    # rewrite
    if not cleaned or upper in _LLM_DROP_MARKERS or upper.startswith("DROP"):
        return None
    return cleaned


def _apply_llm_local(
    df: pd.DataFrame, text_col: str, params: dict[str, Any]
) -> tuple[pd.DataFrame, dict[str, Any]]:
    before = len(df)
    instruction = str(
        params.get("instruction")
        or "请清洗文本，去除噪声，保持原意，只输出清洗后正文。"
    ).strip()
    action = str(params.get("action") or "rewrite").strip().lower()
    if action not in {"rewrite", "filter"}:
        action = "rewrite"
    max_items = max(1, min(int(params.get("max_items") or 50), 500))

    if before == 0:
        return df.copy(), {
            "method": "llm_local",
            "rows_before": 0,
            "rows_after": 0,
            "removed": 0,
            "processed": 0,
            "action": action,
            "model": ANNOTATOR_MODEL,
        }

    work = df.head(max_items).copy()
    rest = df.iloc[max_items:].copy() if before > max_items else df.iloc[0:0].copy()

    kept_rows: list[dict[str, Any]] = []
    dropped = 0
    rewritten = 0
    for _, row in work.iterrows():
        text = row.get(text_col)
        text_s = "" if pd.isna(text) else str(text)
        result = _llm_clean_one(text_s, instruction=instruction, action=action)
        if result is None:
            dropped += 1
            continue
        new_row = row.to_dict()
        if action == "rewrite" and result != text_s:
            new_row[text_col] = result
            rewritten += 1
        elif action == "rewrite":
            new_row[text_col] = result
        kept_rows.append(new_row)

    out = pd.DataFrame(kept_rows) if kept_rows else work.iloc[0:0].copy()
    if not rest.empty:
        # 超出 max_items 的行：默认保留原文（不调用 LLM）
        out = pd.concat([out, rest], ignore_index=True)
    out = out.reset_index(drop=True)
    after = len(out)
    return out, {
        "method": "llm_local",
        "rows_before": before,
        "rows_after": after,
        "removed": before - after,
        "processed": min(before, max_items),
        "dropped": dropped,
        "rewritten": rewritten,
        "action": action,
        "model": ANNOTATOR_MODEL,
        "base_url": ANNOTATOR_BASE_URL,
        "params": {
            "instruction": instruction,
            "action": action,
            "max_items": max_items,
        },
    }


def _apply_one(
    df: pd.DataFrame, text_col: str, method_id: str, params: dict[str, Any]
) -> tuple[pd.DataFrame, dict[str, Any]]:
    before = len(df)
    out = df.copy()
    s = out[text_col]

    if method_id == "strip_whitespace":
        out[text_col] = s.astype(str).map(
            lambda x: "" if x == "nan" else str(x).strip()
        )
    elif method_id == "normalize_whitespace":
        out[text_col] = s.astype(str).map(
            lambda x: "" if x == "nan" else _WS_RE.sub(" ", str(x)).strip()
        )
    elif method_id == "drop_empty":
        mask = s.astype(str).map(
            lambda x: bool(str(x).strip()) and str(x).strip().lower() != "nan"
        )
        out = out.loc[mask].reset_index(drop=True)
    elif method_id == "dedupe_exact":
        out = out.drop_duplicates(subset=[text_col], keep="first").reset_index(
            drop=True
        )
    elif method_id == "dedupe_normalized":
        key = (
            s.astype(str)
            .map(lambda x: "" if x == "nan" else _WS_RE.sub(" ", str(x)).strip())
        )
        out = out.assign(_norm_key=key)
        out = out.drop_duplicates(subset=["_norm_key"], keep="first").drop(
            columns=["_norm_key"]
        )
        out = out.reset_index(drop=True)
    elif method_id == "min_length":
        min_len = int(params.get("min_len", 2))
        mask = s.astype(str).map(
            lambda x: len(str(x).strip()) >= min_len and str(x).strip().lower() != "nan"
        )
        out = out.loc[mask].reset_index(drop=True)
    elif method_id == "max_length":
        max_len = int(params.get("max_len", 5000))
        mask = s.astype(str).map(lambda x: len(str(x)) <= max_len)
        out = out.loc[mask].reset_index(drop=True)
    elif method_id == "remove_urls":
        out[text_col] = s.astype(str).map(
            lambda x: "" if x == "nan" else _URL_RE.sub(" ", str(x)).strip()
        )
        out[text_col] = out[text_col].map(lambda x: _WS_RE.sub(" ", x).strip())
    elif method_id == "remove_html":
        out[text_col] = s.astype(str).map(
            lambda x: "" if x == "nan" else _HTML_RE.sub(" ", str(x)).strip()
        )
        out[text_col] = out[text_col].map(lambda x: _WS_RE.sub(" ", x).strip())
    elif method_id == "to_lower":
        out[text_col] = s.astype(str).map(
            lambda x: "" if x == "nan" else str(x).lower()
        )
    elif method_id == "drop_null_rows":
        out = out.dropna(how="all").reset_index(drop=True)
    elif method_id == "llm_local":
        return _apply_llm_local(df, text_col, params or {})
    else:
        raise ValueError(f"未知清洗方法: {method_id}")

    after = len(out)
    return out, {
        "method": method_id,
        "rows_before": before,
        "rows_after": after,
        "removed": before - after,
    }


def run_clean(
    session_id: str,
    methods: list[dict[str, Any]],
    *,
    filter_spec: dict[str, Any] | None = None,
    db=None,
) -> dict[str, Any]:
    sess = get_session(session_id)
    # 允许仅检索筛选（methods 为空）或仅清洗
    known = {m["id"] for m in list_methods()}
    df = sess.df.copy()
    text_col = sess.text_col
    if text_col not in df.columns:
        raise ValueError(f"文本列 {text_col} 不存在")

    steps: list[dict[str, Any]] = []

    # 1) 可选检索筛选（复用数据检索能力）
    if filter_spec and str(filter_spec.get("query") or "").strip():
        if db is None:
            raise ValueError("检索筛选需要数据库会话")
        df, freport = _apply_search_filter(db, sess, df, filter_spec)
        if freport:
            steps.append(freport)

    # 2) 规则 / 本地大模型清洗
    applied = 0
    for step in methods or []:
        mid = (step.get("id") or step.get("method") or "").strip()
        if not mid:
            continue
        if mid not in known:
            raise ValueError(f"未知清洗方法: {mid}")
        params = step.get("params") or {}
        df, report = _apply_one(df, text_col, mid, params)
        steps.append({**report, "params": params if mid != "llm_local" else report.get("params") or params})
        applied += 1

    if not steps:
        raise ValueError("请填写检索条件，或至少选择一种清洗方法")

    sess.cleaned_df = df
    report = {
        "session_id": session_id,
        "source_name": sess.source_name,
        "dataset_id": sess.dataset_id,
        "text_col": text_col,
        "before": _stats(sess.df, text_col),
        "after": _stats(df, text_col),
        "steps": steps,
        "preview": _preview_df(df),
        "filter": filter_spec if filter_spec and str(filter_spec.get("query") or "").strip() else None,
        "llm": local_llm_info(),
        "methods_applied": applied,
    }
    sess.last_report = report
    return report


def export_cleaned(
    session_id: str, *, fmt: str = "csv"
) -> tuple[Path, str, str]:
    """返回 (path, media_type, filename)。优先导出清洗结果，否则原始。"""
    ensure_data_dirs()
    sess = get_session(session_id)
    df = sess.cleaned_df if sess.cleaned_df is not None else sess.df
    fmt = (fmt or "csv").lower().strip()
    base = f"cleaned_{sess.session_id[:8]}"
    if fmt in {"xlsx", "excel"}:
        out = EXPORT_DIR / f"{base}.xlsx"
        df.to_excel(out, index=False, engine="openpyxl")
        return out, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", f"{base}.xlsx"
    out = EXPORT_DIR / f"{base}.csv"
    df.to_csv(out, index=False, encoding="utf-8-sig")
    return out, "text/csv; charset=utf-8", f"{base}.csv"
