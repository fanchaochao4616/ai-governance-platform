"""Managed dataset APIs: 训练文件包 + 向量检索。"""

from __future__ import annotations

import shutil
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db import get_db
from app.services import dataset_clean_ops as dclean
from app.services import dataset_manage_service as dms
from config import UPLOAD_DIR, ensure_data_dirs

router = APIRouter(prefix="/api/v1/datasets", tags=["datasets"])


class DatasetMetaUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=256)
    description: str | None = None
    modality: str | None = None


class DatasetSearchBody(BaseModel):
    """多模式检索。

    mode:
      - keywords: （多）关键词，空格/逗号分隔，或传 keywords 数组
      - regex: 正则
      - vector: 向量 embedding 相似检索
    """

    mode: str = Field(
        default="keywords",
        description="keywords | regex | vector_fast(TF-IDF) | vector(BGE语义)",
    )
    query: str = Field(default="", description="查询串 / 正则 / 向量 query")
    keywords: list[str] | None = Field(
        default=None, description="多关键词数组（可选，优先于 query 拆分）"
    )
    match: str = Field(default="any", description="多关键词 any|all")
    case_sensitive: bool = Field(default=False, description="关键词/正则是否区分大小写")
    top_k: int = Field(default=20, ge=1, le=200, description="vector 模式返回条数")
    min_score: float | None = Field(
        default=None,
        description="向量模式 score 下限（可选）。先按 score≥阈值过滤，再取 Top-K；不传则不过滤",
    )
    limit: int = Field(default=100, ge=1, le=500, description="关键词/正则最大条数")


@router.get("/modalities")
def modalities() -> dict:
    return {"modalities": dms.list_modality_options()}


@router.post("/inspect-upload")
async def inspect_upload(file: UploadFile = File(...)) -> dict:
    ensure_data_dirs()
    original = file.filename or "upload.bin"
    suf = Path(original).suffix.lower()
    if suf not in {".csv", ".xlsx", ".xlsm", ".xls"}:
        raise HTTPException(400, "文本模态仅支持 .csv / .xlsx / .xls")
    tmp = UPLOAD_DIR / f"ds_inspect_{uuid.uuid4().hex}{suf}"
    try:
        with tmp.open("wb") as f:
            shutil.copyfileobj(file.file, f)
        return dms.inspect_upload_file(tmp)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, f"解析失败: {e}") from e
    finally:
        try:
            tmp.unlink(missing_ok=True)  # type: ignore[arg-type]
        except Exception:  # noqa: BLE001
            pass


@router.get("")
def list_datasets(
    q: str | None = None,
    modality: str | None = None,
    db: Session = Depends(get_db),
) -> list[dict]:
    items = dms.list_datasets(db, q=q, modality=modality)
    return [dms.to_out(db, x) for x in items]


@router.get("/{dataset_id}")
def get_dataset(
    dataset_id: int,
    preview: bool = Query(default=True),
    db: Session = Depends(get_db),
) -> dict:
    ds = dms.get_dataset(db, dataset_id)
    if not ds:
        raise HTTPException(404, "dataset not found")
    return dms.to_out(db, ds, include_preview=preview)


@router.post("")
async def create_dataset(
    name: str = Form(...),
    description: str = Form(default=""),
    modality: str = Form(default="text"),
    id_column: str = Form(default=""),
    text_column: str = Form(default=""),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
) -> dict:
    """创建训练文件包（JSONL/CSV）并自动建向量索引。"""
    ensure_data_dirs()
    if file is None or not (file.filename or "").strip():
        raise HTTPException(400, "请上传数据文件")
    if not (text_column or "").strip():
        raise HTTPException(400, "请选择 text 列")
    original = file.filename or "upload.bin"
    suf = Path(original).suffix.lower()
    if modality.strip().lower() == "text" and suf not in {
        ".csv",
        ".xlsx",
        ".xlsm",
        ".xls",
    }:
        raise HTTPException(400, "文本模态仅支持 .csv / .xlsx / .xls")
    upload_path = UPLOAD_DIR / f"ds_up_{uuid.uuid4().hex}{suf}"
    try:
        with upload_path.open("wb") as f:
            shutil.copyfileobj(file.file, f)
        ds = dms.create_dataset(
            db,
            name=name,
            description=description or None,
            modality=modality,
            upload_path=upload_path,
            original_filename=original,
            id_column=id_column or None,
            text_column=text_column or None,
            build_vectors=True,
        )
        out = dms.to_out(db, ds, include_preview=True)
        dropped = getattr(ds, "_dropped_empty_rows", 0) or 0
        if dropped:
            out["dropped_empty_rows"] = int(dropped)
            out["map_note"] = f"已丢弃 {int(dropped)} 行空文本"
        return out
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, f"创建失败: {e}") from e
    finally:
        try:
            upload_path.unlink(missing_ok=True)  # type: ignore[arg-type]
        except Exception:  # noqa: BLE001
            pass


@router.patch("/{dataset_id}")
def update_dataset(
    dataset_id: int,
    body: DatasetMetaUpdate,
    db: Session = Depends(get_db),
) -> dict:
    ds = dms.get_dataset(db, dataset_id)
    if not ds:
        raise HTTPException(404, "dataset not found")
    try:
        kwargs: dict = {}
        if body.name is not None:
            kwargs["name"] = body.name
        if "description" in body.model_fields_set:
            kwargs["description"] = body.description
        if body.modality is not None:
            kwargs["modality"] = body.modality
        ds = dms.update_dataset_meta(db, ds, **kwargs)
        return dms.to_out(db, ds, include_preview=True)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@router.delete("/{dataset_id}")
def delete_dataset(dataset_id: int, db: Session = Depends(get_db)) -> dict:
    ds = dms.get_dataset(db, dataset_id)
    if not ds:
        raise HTTPException(404, "dataset not found")
    dms.delete_dataset(db, ds)
    return {"ok": True, "deleted": dataset_id}


@router.get("/{dataset_id}/records")
def list_dataset_records(
    dataset_id: int,
    limit: int = Query(default=500, ge=1, le=5000),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> dict:
    """浏览数据集全部样本（数据检索页默认展示）。"""
    ds = dms.get_dataset(db, dataset_id)
    if not ds:
        raise HTTPException(404, "dataset not found")
    try:
        return dms.list_dataset_records(db, ds, limit=limit, offset=offset)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@router.get("/{dataset_id}/download")
def download_dataset(dataset_id: int, db: Session = Depends(get_db)) -> Response:
    """下载训练用 CSV（来自文件包 data.csv）。"""
    ds = dms.get_dataset(db, dataset_id)
    if not ds:
        raise HTTPException(404, "dataset not found")
    try:
        content = dms.export_dataset_csv_bytes(db, ds)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    filename = dms.download_filename(ds)
    from urllib.parse import quote

    encoded = quote(filename)
    headers = {
        "Content-Disposition": (
            f"attachment; filename=\"{filename}\"; filename*=UTF-8''{encoded}"
        )
    }
    return Response(
        content=content,
        media_type="text/csv; charset=utf-8",
        headers=headers,
    )


@router.post("/{dataset_id}/reindex")
def reindex_dataset(dataset_id: int, db: Session = Depends(get_db)) -> dict:
    """重建向量索引。"""
    ds = dms.get_dataset(db, dataset_id)
    if not ds:
        raise HTTPException(404, "dataset not found")
    try:
        cfg = dms.rebuild_vectors(db, ds)
        out = dms.to_out(db, ds, include_preview=False)
        out["index"] = cfg
        return out
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, f"重建索引失败: {e}") from e


@router.post("/{dataset_id}/search")
def search_dataset(
    dataset_id: int,
    body: DatasetSearchBody,
    db: Session = Depends(get_db),
) -> dict:
    """多模式检索：keywords / regex / vector。"""
    ds = dms.get_dataset(db, dataset_id)
    if not ds:
        raise HTTPException(404, "dataset not found")
    try:
        return dms.search_dataset_advanced(
            db,
            ds,
            mode=body.mode,
            query=body.query or "",
            keywords=body.keywords,
            match=body.match,
            case_sensitive=body.case_sensitive,
            top_k=body.top_k,
            limit=body.limit,
            min_score=body.min_score,
        )
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, f"检索失败: {e}") from e


class DatasetCleanMatchBody(BaseModel):
    """清洗匹配条件。所有方式均支持 invert（条件反选）。"""

    method: str = Field(
        default="keywords",
        description="keywords | regex | vector_fast | vector | llm",
    )
    query: str = Field(default="", description="清洗条件")
    keywords: list[str] | None = None
    match: str = Field(default="any", description="关键词 any|all")
    case_sensitive: bool = False
    invert: bool = Field(
        default=False,
        description="条件反选：true=删除未匹配样本；false=删除匹配样本",
    )
    top_k: int = Field(
        default=50,
        ge=1,
        le=10000,
        description="兼容字段；清洗向量/大模型已不再用 Top-K 截断，可忽略",
    )
    min_score: float | None = Field(
        default=None,
        description="兼容字段；清洗匹配不再用后端阈值，结果阈值由前端本地筛选",
    )
    label: str = Field(default="", description="本次清洗备注（可选）")
    selected_ids: list[str] | None = Field(
        default=None,
        description="前端勾选后的最终删除 id 列表；不传则删除整次匹配结果",
    )


@router.get("/{dataset_id}/clean")
def clean_overview(dataset_id: int, db: Session = Depends(get_db)) -> dict:
    """清洗概览：原始/生效/已删 + 历史批次。"""
    try:
        return dclean.get_clean_overview(db, dataset_id)
    except ValueError as e:
        msg = str(e)
        raise HTTPException(404 if "not found" in msg else 400, msg) from e


@router.get("/{dataset_id}/clean/records")
def clean_records(
    dataset_id: int,
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> dict:
    """当前生效样本（原始 − 未恢复的删除 id）。"""
    try:
        return dclean.list_effective_records(
            db, dataset_id, limit=limit, offset=offset
        )
    except ValueError as e:
        msg = str(e)
        raise HTTPException(404 if "not found" in msg else 400, msg) from e


@router.post("/{dataset_id}/clean/preview")
def clean_preview(
    dataset_id: int,
    body: DatasetCleanMatchBody,
    db: Session = Depends(get_db),
) -> dict:
    """预览匹配（含反选），不写入删除。"""
    try:
        return dclean.match_records(
            db,
            dataset_id,
            method=body.method,
            query=body.query or "",
            keywords=body.keywords,
            match=body.match,
            case_sensitive=body.case_sensitive,
            invert=body.invert,
            top_k=body.top_k,
            min_score=body.min_score,
        )
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, f"预览失败: {e}") from e


@router.post("/{dataset_id}/clean/apply")
def clean_apply(
    dataset_id: int,
    body: DatasetCleanMatchBody,
    db: Session = Depends(get_db),
) -> dict:
    """应用删除：只记录 id 到 clean/ops，不复制/改写原始 data.jsonl。"""
    try:
        return dclean.apply_delete(
            db,
            dataset_id,
            method=body.method,
            query=body.query or "",
            keywords=body.keywords,
            match=body.match,
            case_sensitive=body.case_sensitive,
            invert=body.invert,
            top_k=body.top_k,
            min_score=body.min_score,
            label=body.label or "",
            selected_ids=body.selected_ids,
        )
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, f"清洗失败: {e}") from e


@router.get("/{dataset_id}/clean/ops/{op_id}")
def clean_op_diff(
    dataset_id: int,
    op_id: str,
    db: Session = Depends(get_db),
) -> dict:
    """查看某次清洗的 diff（删除样本快照 + 前后计数）。"""
    try:
        return dclean.get_op_diff(db, dataset_id, op_id)
    except ValueError as e:
        msg = str(e)
        raise HTTPException(404 if "不存在" in msg or "not found" in msg else 400, msg) from e


@router.post("/{dataset_id}/clean/ops/{op_id}/restore")
def clean_restore_op(
    dataset_id: int,
    op_id: str,
    db: Session = Depends(get_db),
) -> dict:
    """回退某一清洗批次（撤销该批删除的 id）。"""
    try:
        return dclean.restore_op(db, dataset_id, op_id)
    except ValueError as e:
        msg = str(e)
        raise HTTPException(404 if "不存在" in msg or "not found" in msg else 400, msg) from e


class DatasetCleanExportDatasetBody(BaseModel):
    """将当前生效样本导出到数据集库（仅 id 引用，不复制全文）。"""

    name: str = Field(default="", max_length=256, description="新数据集名称")
    description: str | None = Field(default=None, description="说明（可选）")
    build_vectors: bool = Field(default=True, description="是否构建语义向量索引")


@router.post("/{dataset_id}/clean/export-dataset")
def clean_export_to_library(
    dataset_id: int,
    body: DatasetCleanExportDatasetBody,
    db: Session = Depends(get_db),
) -> dict:
    """导出到数据集库：只存生效样本 id 列表（id_ref），正文从源数据集解析。"""
    try:
        return dclean.save_effective_as_dataset(
            db,
            dataset_id,
            name=body.name or "",
            description=body.description,
            build_vectors=body.build_vectors,
        )
    except ValueError as e:
        msg = str(e)
        raise HTTPException(404 if "not found" in msg else 400, msg) from e
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, f"导出到数据集库失败: {e}") from e
