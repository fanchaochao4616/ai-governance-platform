"""数据集管理：SQL 元数据 + 训练文件包 + 向量检索。

训练主路径（不要用 SQL 读样本训练）::

    data/datasets/{id}/data.jsonl
    data/datasets/{id}/data.csv
    data/datasets/{id}/manifest.json
    data/datasets/{id}/vectors/
    data/datasets/{id}/media/   # 未来图片
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd
from sqlalchemy.orm import Session

from app.models import ManagedDataset, ManagedDatasetRow
from app.services import dataset_store as store
from app.services import dataset_vector as dvec
from app.services.io_tabular import ID_ALIASES, TEXT_ALIASES, read_tabular
from config import DATASET_DIR, ensure_data_dirs

SUPPORTED_MODALITIES = (
    {
        "id": "text",
        "name": "文本",
        "enabled": True,
        "formats": [".csv", ".xlsx", ".xlsm", ".xls"],
    },
    {"id": "image", "name": "图像", "enabled": False, "formats": []},
    {"id": "audio", "name": "音频", "enabled": False, "formats": []},
    {"id": "video", "name": "视频", "enabled": False, "formats": []},
)

TEXT_FILE_SUFFIXES = {".csv", ".xlsx", ".xlsm", ".xls"}
STORAGE_FILES = "files"


def list_modality_options() -> list[dict[str, Any]]:
    return list(SUPPORTED_MODALITIES)


def _detect_text_col(df: pd.DataFrame) -> str | None:
    for col in df.columns:
        key = str(col).strip()
        low = key.lower()
        if low in TEXT_ALIASES or key in TEXT_ALIASES:
            return str(col)
    for col in df.columns:
        key = str(col).strip()
        low = key.lower()
        for alias in TEXT_ALIASES:
            a = str(alias)
            if a and (a in key or a.lower() in low):
                return str(col)
    if len(df.columns) > 0:
        return str(df.columns[0])
    return None


def _detect_id_col(df: pd.DataFrame, *, exclude: str | None = None) -> str | None:
    id_alias_low = {a.lower() for a in ID_ALIASES}
    for col in df.columns:
        key = str(col).strip()
        if exclude is not None and str(col) == exclude:
            continue
        low = key.lower()
        if low in id_alias_low or key in ID_ALIASES:
            return str(col)
    for col in df.columns:
        key = str(col).strip()
        if exclude is not None and str(col) == exclude:
            continue
        low = key.lower()
        for alias in ID_ALIASES:
            a = str(alias)
            if not a:
                continue
            if a in key or a.lower() in low:
                return str(col)
        if low.endswith("_id") or low.endswith("id"):
            return str(col)
    return None


def _file_format_of(path: Path) -> str:
    suf = path.suffix.lower()
    if suf == ".csv":
        return "csv"
    if suf in {".xlsx", ".xlsm", ".xls"}:
        return "xlsx"
    return suf.lstrip(".") or "unknown"


def _inspect_text_file(path: Path) -> dict[str, Any]:
    df = read_tabular(path)
    if df is None or df.empty:
        return {
            "row_count": 0,
            "column_count": 0,
            "columns": [],
            "id_column": None,
            "text_column": None,
            "status": "empty",
            "error_message": "文件为空",
            "preview": [],
        }
    df = df.copy()
    df.columns = [str(c) for c in df.columns]
    text_col = _detect_text_col(df)
    id_col = _detect_id_col(df, exclude=text_col)
    preview = []
    for _, row in df.head(10).iterrows():
        item: dict[str, Any] = {}
        for c in df.columns:
            v = row[c]
            if pd.isna(v):
                item[str(c)] = None
            else:
                item[str(c)] = v if isinstance(v, (int, float, bool)) else str(v)
        preview.append(item)
    return {
        "row_count": int(len(df)),
        "column_count": int(len(df.columns)),
        "columns": [str(c) for c in df.columns],
        "id_column": id_col,
        "text_column": text_col,
        "status": "ready",
        "error_message": None,
        "preview": preview,
    }


def _map_records_from_df(
    df: pd.DataFrame,
    *,
    id_column: str | None,
    text_column: str,
    modality: str = "text",
) -> tuple[list[dict[str, Any]], int]:
    text_column = (text_column or "").strip()
    if not text_column:
        raise ValueError("请选择 text 列")
    id_column = (id_column or "").strip() or None
    if id_column and id_column == text_column:
        raise ValueError("id 列与 text 列不能相同")

    df = df.copy()
    df.columns = [str(c) for c in df.columns]
    cols = set(df.columns)
    if text_column not in cols:
        raise ValueError(f"找不到 text 列「{text_column}」，当前列: {list(df.columns)}")
    if id_column and id_column not in cols:
        raise ValueError(f"找不到 id 列「{id_column}」，当前列: {list(df.columns)}")

    records: list[dict[str, Any]] = []
    dropped = 0
    for src_i, (_, row) in enumerate(df.iterrows()):
        text_val = row.get(text_column)
        if pd.isna(text_val) or str(text_val).strip() == "":
            dropped += 1
            continue
        text_s = str(text_val).strip()
        ext = None
        if id_column:
            raw = row.get(id_column)
            if not pd.isna(raw):
                s = str(raw).strip()
                ext = s or None
        records.append(
            {
                "id": ext,
                "text": text_s,
                "modality": modality,
                "uri": None,
                "meta": {"source_row": int(src_i) + 2},
            }
        )
    if not records:
        raise ValueError("选定 text 列全部为空，无法创建数据集")
    return records, dropped


def _root_of(ds: ManagedDataset) -> Path:
    if ds.root_path:
        p = Path(ds.root_path)
        if p.exists():
            return p
    return store.dataset_root(ds.id)


def package_path(ds: ManagedDataset) -> Path:
    return _root_of(ds)


def to_out(
    db: Session,
    ds: ManagedDataset,
    *,
    include_preview: bool = False,
) -> dict[str, Any]:
    root = _root_of(ds)
    has_data = store.count_records(root) > 0 if root.exists() else False
    if not has_data and int(ds.row_count or 0) > 0:
        # 尝试从旧 SQL 行 / 旧文件迁文件包
        ensure_file_package(db, ds)
        root = _root_of(ds)
        has_data = store.count_records(root) > 0 if root.exists() else False

    vstat = dvec.index_status(root) if root.exists() else {"ready": False, "n": 0}
    backend = (ds.storage_backend or STORAGE_FILES).strip().lower()
    if backend == "sql":
        backend = STORAGE_FILES

    out: dict[str, Any] = {
        "id": ds.id,
        "name": ds.name,
        "description": ds.description,
        "modality": ds.modality or "text",
        "file_format": ds.file_format or "csv",
        "original_filename": ds.original_filename,
        "storage": backend,
        "storage_backend": backend,
        "root_path": str(root) if root.exists() else ds.root_path,
        "train_paths": {
            "jsonl": str(root / store.JSONL_NAME) if root.exists() else None,
            "csv": str(root / store.CSV_NAME) if root.exists() else None,
            "manifest": str(root / store.MANIFEST_NAME) if root.exists() else None,
            "media": str(root / store.MEDIA_DIR) if root.exists() else None,
            "vectors": str(root / store.VECTORS_DIR) if root.exists() else None,
        },
        "has_raw_archive": bool(ds.raw_archive_path),
        "source_id_column": ds.source_id_column,
        "source_text_column": ds.source_text_column,
        "id_column": ds.id_column,
        "text_column": ds.text_column,
        "row_count": int(ds.row_count or 0),
        "column_count": int(ds.column_count or 0),
        "columns": ds.columns or [],
        "vector_ready": bool(vstat.get("ready") or ds.vector_ready),
        "vector_model": vstat.get("model") or ds.vector_model,
        "vector_dim": vstat.get("dim") or ds.vector_dim,
        "vector_count": int(vstat.get("n") or ds.vector_count or 0),
        "status": ds.status or "empty",
        "error_message": ds.error_message,
        "created_at": ds.created_at.isoformat() if ds.created_at else None,
        "updated_at": ds.updated_at.isoformat() if ds.updated_at else None,
        "has_file": has_data,
    }
    if include_preview and has_data:
        try:
            out["preview"] = store.preview_records(root, 20)
        except Exception as e:  # noqa: BLE001
            out["preview"] = []
            out["preview_error"] = str(e)
    elif include_preview:
        out["preview"] = []
    return out


def list_datasets(
    db: Session, *, q: str | None = None, modality: str | None = None
) -> list[ManagedDataset]:
    query = db.query(ManagedDataset)
    if modality:
        query = query.filter(ManagedDataset.modality == modality.strip().lower())
    if q and q.strip():
        like = f"%{q.strip()}%"
        query = query.filter(
            (ManagedDataset.name.ilike(like))
            | (ManagedDataset.description.ilike(like))
            | (ManagedDataset.original_filename.ilike(like))
        )
    return query.order_by(ManagedDataset.id.desc()).all()


def get_dataset(db: Session, dataset_id: int) -> ManagedDataset | None:
    return db.get(ManagedDataset, dataset_id)


def inspect_upload_file(path: Path) -> dict[str, Any]:
    path = Path(path)
    if not path.exists():
        raise ValueError("上传文件不存在")
    if path.suffix.lower() not in TEXT_FILE_SUFFIXES:
        raise ValueError("仅支持 .csv / .xlsx / .xls")
    try:
        meta = _inspect_text_file(path)
    except Exception as e:  # noqa: BLE001
        raise ValueError(f"解析失败: {e}") from e
    return {
        "row_count": meta.get("row_count", 0),
        "column_count": meta.get("column_count", 0),
        "columns": meta.get("columns") or [],
        "id_column": meta.get("id_column"),
        "text_column": meta.get("text_column"),
        "status": meta.get("status"),
        "error_message": meta.get("error_message"),
        "preview": meta.get("preview") or [],
        "file_format": _file_format_of(path),
    }


def create_dataset(
    db: Session,
    *,
    name: str,
    description: str | None = None,
    modality: str = "text",
    upload_path: Path | None = None,
    original_filename: str | None = None,
    id_column: str | None = None,
    text_column: str | None = None,
    build_vectors: bool = True,
) -> ManagedDataset:
    """解析上传 → 写训练文件包（JSONL/CSV）→ 建向量索引。"""
    ensure_data_dirs()
    mod = (modality or "text").strip().lower()
    allowed = {m["id"]: m for m in SUPPORTED_MODALITIES}
    if mod not in allowed:
        raise ValueError(f"不支持的模态: {modality}")
    if not allowed[mod]["enabled"]:
        raise ValueError(f"模态「{allowed[mod]['name']}」暂未实现，请选择文本")

    name = (name or "").strip()
    if not name:
        raise ValueError("名称不能为空")
    if upload_path is None:
        raise ValueError("请上传数据文件")
    if mod != "text":
        raise ValueError("当前仅支持文本模态上传；图片请后续走 media/ 结构")

    text_col = (text_column or "").strip() or None
    id_col = (id_column or "").strip() or None
    if not text_col:
        raise ValueError("请选择 text 列")
    if id_col and id_col == text_col:
        raise ValueError("id 列与 text 列不能相同")

    path = Path(upload_path)
    if not path.exists():
        raise ValueError("上传文件不存在")
    if path.suffix.lower() not in TEXT_FILE_SUFFIXES:
        raise ValueError("文本模态仅支持 .csv / .xlsx / .xls")

    try:
        df = read_tabular(path)
    except Exception as e:  # noqa: BLE001
        raise ValueError(f"解析失败: {e}") from e
    if df is None or df.empty:
        raise ValueError("文件为空")

    records, dropped = _map_records_from_df(
        df, id_column=id_col, text_column=text_col, modality=mod
    )
    orig_name = original_filename or path.name

    ds = ManagedDataset(
        name=name,
        description=(description or "").strip() or None,
        modality=mod,
        status="empty",
        original_filename=orig_name,
        file_format=_file_format_of(path),
        storage_backend=STORAGE_FILES,
        source_id_column=id_col,
        source_text_column=text_col,
    )
    db.add(ds)
    db.flush()
    dataset_id = int(ds.id)
    root = store.dataset_root(dataset_id)

    try:
        store.ensure_package_dirs(root)
        raw_rel = store.archive_raw(root, path, orig_name)
        stats = store.write_records(root, records)
        cols = ["id", "text"] if id_col else ["text"]
        manifest = {
            "dataset_id": dataset_id,
            "name": name,
            "modality": mod,
            "format": "jsonl+csv",
            "train_hint": "Use data.jsonl for training (one sample per line).",
            "schema": {
                "id": "optional external id",
                "text": "main text field",
                "modality": "text|image|audio|video",
                "uri": "relative path under media/ for binaries",
            },
            "row_count": stats["row_count"],
            "source_id_column": id_col,
            "source_text_column": text_col,
            "original_filename": orig_name,
            "raw": raw_rel,
            "files": {
                "jsonl": store.JSONL_NAME,
                "csv": store.CSV_NAME,
                "media": store.MEDIA_DIR,
                "vectors": store.VECTORS_DIR,
            },
        }
        store.write_manifest(root, manifest)

        ds.root_path = str(root)
        ds.raw_archive_path = raw_rel
        ds.storage_backend = STORAGE_FILES
        ds.storage_path = None
        ds.row_count = int(stats["row_count"])
        ds.column_count = len(cols)
        ds.columns = cols
        ds.id_column = "id" if id_col else None
        ds.text_column = "text"
        ds.status = "ready"
        ds.error_message = None

        if build_vectors:
            ds.status = "indexing"
            db.commit()
            try:
                # 语义向量：中文模型 BAAI/bge-small-zh-v1.5
                cfg = dvec.build_index(root, kind="model")
                ds.vector_ready = True
                ds.vector_model = str(cfg.get("model") or "BAAI/bge-small-zh-v1.5")
                ds.vector_dim = int(cfg.get("dim") or 0) if cfg.get("dim") else None
                ds.vector_count = int(cfg.get("n") or 0)
                store.write_manifest(
                    root,
                    {
                        **manifest,
                        "vector": {
                            "ready": True,
                            "model": ds.vector_model,
                            "dim": ds.vector_dim,
                            "n": ds.vector_count,
                            "provider": "model",
                            "label": cfg.get("label"),
                        },
                    },
                )
            except Exception as ve:  # noqa: BLE001
                # 创建数据集不因向量失败而整单失败；语义检索时再强制建索引
                ds.vector_ready = False
                ds.vector_model = None
                ds.error_message = f"语义索引未就绪: {ve}"
            ds.status = "ready"
        else:
            ds.vector_ready = False

        db.commit()
        db.refresh(ds)
        ds._dropped_empty_rows = dropped  # type: ignore[attr-defined]
        return ds
    except Exception:
        db.rollback()
        store.delete_package(dataset_id)
        try:
            orphan = db.get(ManagedDataset, dataset_id)
            if orphan is not None:
                db.delete(orphan)
                db.commit()
        except Exception:  # noqa: BLE001
            db.rollback()
        raise


def update_dataset_meta(
    db: Session,
    ds: ManagedDataset,
    *,
    name: str | None = None,
    description: str | None = ...,  # type: ignore[assignment]
    modality: str | None = None,
) -> ManagedDataset:
    if name is not None:
        n = name.strip()
        if not n:
            raise ValueError("名称不能为空")
        ds.name = n
    if description is not ...:
        ds.description = (description or "").strip() or None
    if modality is not None:
        mod = modality.strip().lower()
        allowed = {m["id"]: m for m in SUPPORTED_MODALITIES}
        if mod not in allowed:
            raise ValueError(f"不支持的模态: {modality}")
        if not allowed[mod]["enabled"] and mod != (ds.modality or "text"):
            raise ValueError(f"模态「{allowed[mod]['name']}」暂未实现")
        if int(ds.row_count or 0) > 0 and mod != "text":
            raise ValueError("当前数据集已有文本数据，无法直接改为其他模态")
        ds.modality = mod
    # 同步 manifest name
    root = _root_of(ds)
    if root.exists():
        man = store.read_manifest(root) or {}
        man["name"] = ds.name
        man["description"] = ds.description
        store.write_manifest(root, man)
    db.commit()
    db.refresh(ds)
    return ds


def delete_dataset(db: Session, ds: ManagedDataset) -> None:
    # 清理旧 SQL 行（若有）
    db.query(ManagedDatasetRow).filter(
        ManagedDatasetRow.dataset_id == ds.id
    ).delete(synchronize_session=False)
    store.delete_package(ds.id)
    # 旧版散落文件
    if DATASET_DIR.exists():
        for p in DATASET_DIR.glob(f"ds_{ds.id}_*"):
            try:
                if p.is_file():
                    p.unlink()
            except OSError:
                pass
    db.delete(ds)
    db.commit()


def export_dataset_csv_bytes(db: Session, ds: ManagedDataset) -> bytes:
    ensure_file_package(db, ds)
    root = _root_of(ds)
    if not store.package_exists(ds.id) and not (root / store.JSONL_NAME).exists():
        raise ValueError("数据集无训练文件包")
    return store.csv_bytes_from_package(root)


def download_filename(ds: ManagedDataset) -> str:
    base = Path(ds.original_filename or f"dataset_{ds.id}").stem or f"dataset_{ds.id}"
    if base.endswith("_mapped"):
        base = base[: -len("_mapped")]
    return f"{base}.csv"


def rebuild_vectors(
    db: Session, ds: ManagedDataset, *, kind: str = "model"
) -> dict[str, Any]:
    """重建语义向量索引（中文 embedding 模型）。"""
    ensure_file_package(db, ds)
    root = _root_of(ds)
    if store.count_records(root) == 0:
        raise ValueError("数据集为空，无法建索引")
    ds.status = "indexing"
    db.commit()
    try:
        # 重建：先确保 TF-IDF 快速索引，再强制建 BGE 语义索引（失败则抛错）
        try:
            dvec.build_index(root, kind="tfidf")
        except Exception:  # noqa: BLE001
            pass
        cfg = dvec.build_index(root, kind="model")
        ds.vector_ready = True
        ds.vector_model = str(cfg.get("model") or "BAAI/bge-small-zh-v1.5")
        ds.vector_dim = int(cfg.get("dim") or 0) if cfg.get("dim") else None
        ds.vector_count = int(cfg.get("n") or 0)
        ds.status = "ready"
        ds.error_message = None
        man = store.read_manifest(root) or {}
        man["vector"] = {
            "ready": True,
            "model": ds.vector_model,
            "dim": ds.vector_dim,
            "n": ds.vector_count,
            "provider": "model",
            "built": cfg.get("built"),
            "label": cfg.get("label") or "语义向量(BGE)",
            "tfidf": True,
        }
        store.write_manifest(root, man)
        db.commit()
        db.refresh(ds)
        return cfg
    except Exception as e:  # noqa: BLE001
        ds.status = "ready"
        ds.error_message = f"语义向量索引失败: {e}"
        db.commit()
        raise


def list_dataset_records(
    db: Session,
    ds: ManagedDataset,
    *,
    limit: int = 500,
    offset: int = 0,
) -> dict[str, Any]:
    """浏览数据集样本（数据检索页点选后默认展示）。"""
    ensure_file_package(db, ds)
    root = _root_of(ds)
    all_recs = store.load_records(root)
    total = len(all_recs)
    offset = max(0, int(offset))
    limit = max(1, min(int(limit), 5000))
    slice_ = all_recs[offset : offset + limit]
    hits: list[dict[str, Any]] = []
    for i, rec in enumerate(slice_):
        hits.append(
            {
                "rank": offset + i + 1,
                "seq": offset + i + 1,
                "id": rec.get("id"),
                "text": rec.get("text"),
                "uri": rec.get("uri"),
                "modality": rec.get("modality") or "text",
            }
        )
    return {
        "mode": "browse",
        "dataset_id": ds.id,
        "count": len(hits),
        "total": total,
        "offset": offset,
        "limit": limit,
        "hits": hits,
    }


def search_dataset(
    db: Session,
    ds: ManagedDataset,
    *,
    query: str,
    top_k: int = 10,
) -> list[dict[str, Any]]:
    """语义向量检索（兼容旧调用，默认中文 embedding 模型）。"""
    out = search_dataset_advanced(
        db,
        ds,
        mode="vector",
        query=query,
        top_k=top_k,
    )
    return out.get("hits") or []


def _parse_keywords(query: str, keywords: list[str] | None) -> list[str]:
    if keywords:
        return [k.strip() for k in keywords if k and str(k).strip()]
    raw = (query or "").strip()
    if not raw:
        return []
    # 支持空格 / 中文逗号 / 英文逗号 / | 分隔
    import re as _re

    parts = _re.split(r"[\s,，|；;]+", raw)
    return [p for p in parts if p]


def search_dataset_advanced(
    db: Session,
    ds: ManagedDataset,
    *,
    mode: str = "keywords",
    query: str = "",
    keywords: list[str] | None = None,
    match: str = "any",
    case_sensitive: bool = False,
    top_k: int = 50,
    limit: int = 200,
    min_score: float | None = None,
) -> dict[str, Any]:
    """多模式检索：keywords | regex | vector。

    - keywords：（多）关键词，match=any|all
    - regex：正则
    - vector：向量 embedding 相似检索
    - min_score：向量模式 score 下限（可选）；None 表示不过滤
    """
    ensure_file_package(db, ds)
    root = _root_of(ds)
    mode = (mode or "keywords").strip().lower()
    if mode in {"keyword", "kw"}:
        mode = "keywords"
    # 快速向量（TF-IDF）
    if mode in {"vector_fast", "tfidf", "fast", "lexical", "quick"}:
        mode = "vector_fast"
    # 语义向量（BGE 中文 embedding）
    if mode in {"embedding", "semantic", "vec", "vector_model", "bge"}:
        mode = "vector"
    if mode not in {"keywords", "regex", "vector", "vector_fast"}:
        raise ValueError(
            "mode 仅支持 keywords / regex / vector_fast / vector"
        )

    top_k = max(1, min(int(top_k), 200))
    limit = max(1, min(int(limit), 500))
    match = (match or "any").strip().lower()
    if match not in {"any", "all"}:
        match = "any"

    thr: float | None = None
    if min_score is not None and str(min_score).strip() != "":
        thr = float(min_score)

    if mode == "vector_fast":
        q = (query or "").strip()
        if not q:
            raise ValueError("快速向量检索需要 query 文本")
        # TF-IDF 快速向量，不回退、不混用 BGE
        st = dvec.index_status(root, "tfidf")
        if not st.get("ready"):
            dvec.build_index(root, kind="tfidf")
        hits = dvec.search(root, q, top_k=top_k, kind="tfidf", min_score=thr)
        return {
            "mode": "vector_fast",
            "dataset_id": ds.id,
            "query": q,
            "top_k": top_k,
            "min_score": thr,
            "count": len(hits),
            "total": len(hits),
            "hits": hits,
            "vector_model": "char-ngram-tfidf",
            "vector_ready": True,
            "index_kind": "tfidf",
            "index_label": "快速向量(TF-IDF)",
        }

    if mode == "vector":
        q = (query or "").strip()
        if not q:
            raise ValueError("语义向量检索需要 query 文本")
        # 仅 BGE 中文 embedding；失败直接报错，绝不回退 TF-IDF
        st = dvec.index_status(root, "model")
        if not st.get("ready"):
            try:
                rebuild_vectors(db, ds, kind="model")
            except Exception as e:  # noqa: BLE001
                raise ValueError(
                    "语义向量（BGE）不可用：请先 pip install sentence-transformers torch，"
                    "再点「重建语义索引」下载 BAAI/bge-small-zh-v1.5。"
                    f" 详情: {e}"
                ) from e
        try:
            hits = dvec.search(root, q, top_k=top_k, kind="model", min_score=thr)
        except Exception as e:  # noqa: BLE001
            raise ValueError(
                "语义向量检索失败（未使用 TF-IDF 回退）。"
                f" 请确认已安装 sentence-transformers 且已重建 BGE 索引。详情: {e}"
            ) from e
        # 二次校验：结果必须来自 model 索引
        if hits and hits[0].get("index_kind") not in (None, "model"):
            raise ValueError(
                f"内部错误：语义检索返回了非 model 索引 ({hits[0].get('index_kind')})"
            )
        return {
            "mode": "vector",
            "dataset_id": ds.id,
            "query": q,
            "top_k": top_k,
            "min_score": thr,
            "count": len(hits),
            "total": len(hits),
            "hits": hits,
            "vector_model": (hits[0].get("index_label") if hits else None)
            or ds.vector_model
            or "BAAI/bge-small-zh-v1.5",
            "vector_ready": True,
            "index_kind": "model",
            "index_label": "语义向量(BGE)",
        }

    import re as _re

    records = store.load_records(root)
    hits: list[dict[str, Any]] = []

    if mode == "regex":
        pattern = (query or "").strip()
        if not pattern:
            raise ValueError("正则检索需要 pattern（query 字段）")
        # 正则默认不区分大小写（中文场景无此选项）
        try:
            cre = _re.compile(pattern, _re.IGNORECASE)
        except _re.error as e:
            raise ValueError(f"正则无效: {e}") from e
        all_hits: list[dict[str, Any]] = []
        for i, rec in enumerate(records):
            text = str(rec.get("text") or "")
            m = cre.search(text)
            if not m:
                continue
            all_hits.append(
                {
                    "rank": 0,
                    "score": 1.0,
                    "seq": i + 1,
                    "id": rec.get("id"),
                    "text": rec.get("text"),
                    "uri": rec.get("uri"),
                    "modality": rec.get("modality") or "text",
                    "match_span": [m.start(), m.end()],
                    "match_text": m.group(0),
                }
            )
        total = len(all_hits)
        for i, h in enumerate(all_hits):
            h["rank"] = i + 1
        return {
            "mode": "regex",
            "dataset_id": ds.id,
            "query": pattern,
            "case_sensitive": False,
            "total": total,
            "count": total,
            "hits": all_hits,  # 前端分页
            "scanned": len(records),
        }

    # keywords
    kws = _parse_keywords(query, keywords)
    if not kws:
        raise ValueError("请提供至少一个关键词（空格/逗号分隔，或 keywords 数组）")
    if not case_sensitive:
        kws_cmp = [k.lower() for k in kws]
    else:
        kws_cmp = kws

    all_hits = []
    for i, rec in enumerate(records):
        text = str(rec.get("text") or "")
        hay = text if case_sensitive else text.lower()
        found = [k for k, kc in zip(kws, kws_cmp) if kc in hay]
        if match == "all":
            ok = len(found) == len(kws)
        else:
            ok = len(found) > 0
        if not ok:
            continue
        # 命中数 / 总关键词 作为粗分数
        score = len(found) / max(len(kws), 1)
        all_hits.append(
            {
                "rank": 0,
                "score": float(score),
                "seq": i + 1,
                "id": rec.get("id"),
                "text": rec.get("text"),
                "uri": rec.get("uri"),
                "modality": rec.get("modality") or "text",
                "matched_keywords": found,
            }
        )

    all_hits.sort(key=lambda h: (-float(h.get("score") or 0), int(h.get("seq") or 0)))
    total = len(all_hits)
    for i, h in enumerate(all_hits):
        h["rank"] = i + 1

    return {
        "mode": "keywords",
        "dataset_id": ds.id,
        "query": query,
        "keywords": kws,
        "match": match,
        "case_sensitive": case_sensitive,
        "total": total,
        "count": total,
        "hits": all_hits,
        "scanned": len(records),
    }


def ensure_file_package(db: Session, ds: ManagedDataset) -> bool:
    """保证存在训练文件包；从旧 SQL 行或旧 CSV 迁移。"""
    root = store.dataset_root(ds.id)
    if (root / store.JSONL_NAME).exists():
        if not ds.root_path:
            ds.root_path = str(root)
            ds.storage_backend = STORAGE_FILES
            db.commit()
        return True

    records: list[dict[str, Any]] = []

    # 1) 旧 SQL 行
    rows = (
        db.query(ManagedDatasetRow)
        .filter(ManagedDatasetRow.dataset_id == ds.id)
        .order_by(ManagedDatasetRow.seq.asc())
        .all()
    )
    if rows:
        for r in rows:
            records.append(
                {
                    "id": r.external_id,
                    "text": r.text,
                    "modality": "text",
                    "uri": None,
                    "meta": r.extra or {},
                }
            )

    # 2) 旧 storage_path / ds_{id}_*
    if not records:
        path = None
        if ds.storage_path and Path(ds.storage_path).exists():
            path = Path(ds.storage_path)
        else:
            cands = sorted(
                DATASET_DIR.glob(f"ds_{ds.id}_*"),
                key=lambda x: x.stat().st_mtime,
                reverse=True,
            )
            path = cands[0] if cands else None
        if path is not None:
            try:
                df = read_tabular(path)
                df.columns = [str(c) for c in df.columns]
                id_col = "id" if "id" in df.columns else ds.source_id_column
                text_col = "text" if "text" in df.columns else ds.source_text_column
                if text_col not in list(df.columns):
                    text_col = _detect_text_col(df)
                if id_col and id_col not in list(df.columns):
                    id_col = _detect_id_col(df, exclude=text_col)
                if text_col:
                    records, _ = _map_records_from_df(
                        df, id_column=id_col, text_column=text_col
                    )
            except Exception:  # noqa: BLE001
                records = []

    if not records:
        return False

    store.ensure_package_dirs(root)
    stats = store.write_records(root, records)
    store.write_manifest(
        root,
        {
            "dataset_id": ds.id,
            "name": ds.name,
            "modality": ds.modality or "text",
            "format": "jsonl+csv",
            "row_count": stats["row_count"],
            "migrated": True,
        },
    )
    ds.root_path = str(root)
    ds.storage_backend = STORAGE_FILES
    ds.row_count = int(stats["row_count"])
    ds.columns = ["id", "text"] if any(r.get("id") for r in records) else ["text"]
    ds.column_count = len(ds.columns or [])
    ds.id_column = "id" if "id" in (ds.columns or []) else None
    ds.text_column = "text"
    ds.status = "ready"
    try:
        cfg = dvec.build_index(root)
        ds.vector_ready = True
        ds.vector_model = str(cfg.get("model") or "")
        ds.vector_dim = int(cfg.get("dim") or 0)
        ds.vector_count = int(cfg.get("n") or 0)
    except Exception:  # noqa: BLE001
        ds.vector_ready = False
    db.commit()
    db.refresh(ds)
    return True


def migrate_all_file_datasets_to_sql(db: Session) -> int:
    """启动迁移：旧数据 → 训练文件包（函数名保留兼容 init_db）。"""
    n = 0
    for ds in db.query(ManagedDataset).order_by(ManagedDataset.id.asc()).all():
        root = store.dataset_root(ds.id)
        before = (root / store.JSONL_NAME).exists()
        if ensure_file_package(db, ds) and not before:
            n += 1
    return n
