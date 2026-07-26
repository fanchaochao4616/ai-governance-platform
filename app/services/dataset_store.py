"""训练友好的数据集文件包读写。

目录结构（每个数据集一个目录）::

    data/datasets/{id}/
      manifest.json     # 元数据 / 训练入口说明
      data.jsonl        # 主数据：每行一条样本（训练首选）
      data.csv          # 同内容 CSV（表格工具 / 部分训练脚本）
      raw/              # 原始上传归档
      media/            # 图片/音频等二进制（uri 相对路径）
      vectors/          # 向量索引（embeddings + 配置）

样本 JSONL 字段::

    {"id": "...", "text": "...", "modality": "text", "uri": null, "meta": {}}

图片模态时 text 可为空，uri 指向 media/ 下文件。
"""

from __future__ import annotations

import csv
import json
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator

from config import DATASET_DIR, ensure_data_dirs

MANIFEST_NAME = "manifest.json"
JSONL_NAME = "data.jsonl"
CSV_NAME = "data.csv"
INCLUDE_IDS_NAME = "include_ids.json"
KIND_ID_REF = "id_ref"
RAW_DIR = "raw"
MEDIA_DIR = "media"
VECTORS_DIR = "vectors"


def _stable_sid(rec: dict[str, Any], index: int) -> str:
    """与清洗侧 stable_id 一致：优先业务 id，否则 row:{1-based}。"""
    rid = rec.get("id")
    if rid is not None and str(rid).strip() != "":
        return str(rid)
    return f"row:{index + 1}"


def is_id_ref_package(root: Path) -> bool:
    man = read_manifest(root)
    return bool(man and str(man.get("kind") or "") == KIND_ID_REF)


def write_include_ids(root: Path, ids: list[str]) -> Path:
    ensure_package_dirs(root)
    path = root / INCLUDE_IDS_NAME
    path.write_text(
        json.dumps([str(x) for x in ids], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return path


def read_include_ids(root: Path) -> list[str]:
    path = root / INCLUDE_IDS_NAME
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            data = None
        if isinstance(data, list):
            return [str(x) for x in data if str(x).strip() != ""]
        if isinstance(data, dict) and isinstance(data.get("ids"), list):
            return [str(x) for x in data["ids"] if str(x).strip() != ""]
    man = read_manifest(root) or {}
    raw = man.get("include_ids")
    if isinstance(raw, list):
        return [str(x) for x in raw if str(x).strip() != ""]
    return []


def dataset_root(dataset_id: int) -> Path:
    ensure_data_dirs()
    return DATASET_DIR / str(int(dataset_id))


def ensure_package_dirs(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / RAW_DIR).mkdir(exist_ok=True)
    (root / MEDIA_DIR).mkdir(exist_ok=True)
    (root / VECTORS_DIR).mkdir(exist_ok=True)


def write_manifest(root: Path, payload: dict[str, Any]) -> Path:
    ensure_package_dirs(root)
    data = dict(payload)
    data["updated_at"] = datetime.now(timezone.utc).isoformat()
    path = root / MANIFEST_NAME
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def read_manifest(root: Path) -> dict[str, Any] | None:
    path = root / MANIFEST_NAME
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None


def write_records(root: Path, records: list[dict[str, Any]]) -> dict[str, Any]:
    """写入 data.jsonl + data.csv，返回统计。"""
    ensure_package_dirs(root)
    jsonl_path = root / JSONL_NAME
    csv_path = root / CSV_NAME

    with jsonl_path.open("w", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    has_id = any(r.get("id") not in (None, "") for r in records)
    has_uri = any(r.get("uri") for r in records)
    fieldnames = ["id", "text"] if has_id else ["text"]
    if has_uri:
        fieldnames = list(fieldnames) + ["uri", "modality"]
    with csv_path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        for rec in records:
            row = {k: rec.get(k, "") if rec.get(k) is not None else "" for k in fieldnames}
            w.writerow(row)

    return {
        "row_count": len(records),
        "jsonl_path": str(jsonl_path),
        "csv_path": str(csv_path),
        "has_id": has_id,
        "has_uri": has_uri,
    }


def iter_jsonl(root: Path) -> Iterator[dict[str, Any]]:
    path = root / JSONL_NAME
    if not path.exists():
        return
        yield  # pragma: no cover
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def _load_records_plain(root: Path, *, limit: int | None = None) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for rec in iter_jsonl(root):
        out.append(rec)
        if limit is not None and len(out) >= limit:
            break
    return out


def _load_records_id_ref(
    root: Path,
    man: dict[str, Any],
    *,
    limit: int | None = None,
    _depth: int = 0,
) -> list[dict[str, Any]]:
    """仅存 id 的引用包：从源数据集解析正文，不落地复制全文。"""
    if _depth > 6:
        raise ValueError("id_ref 引用嵌套过深")
    try:
        src_id = int(man.get("source_dataset_id"))
    except (TypeError, ValueError) as e:
        raise ValueError("id_ref 包缺少合法 source_dataset_id") from e
    include_list = read_include_ids(root)
    if not include_list:
        return []
    include_set = set(include_list)
    src_root = dataset_root(src_id)
    if not src_root.exists():
        raise ValueError(f"引用源数据集 #{src_id} 目录不存在")
    # 源也可以是 id_ref，递归解析
    src_recs = load_records(src_root, _depth=_depth + 1)
    by_sid: dict[str, dict[str, Any]] = {}
    for i, rec in enumerate(src_recs):
        sid = _stable_sid(rec, i)
        if sid in include_set and sid not in by_sid:
            item = dict(rec)
            item["id"] = sid
            by_sid[sid] = item
    out: list[dict[str, Any]] = []
    for sid in include_list:
        if sid in by_sid:
            out.append(by_sid[sid])
            if limit is not None and len(out) >= limit:
                break
    return out


def load_records(
    root: Path, *, limit: int | None = None, _depth: int = 0
) -> list[dict[str, Any]]:
    if is_id_ref_package(root):
        man = read_manifest(root) or {}
        return _load_records_id_ref(root, man, limit=limit, _depth=_depth)
    return _load_records_plain(root, limit=limit)


def count_records(root: Path) -> int:
    if is_id_ref_package(root):
        man = read_manifest(root) or {}
        n = man.get("row_count")
        if isinstance(n, int) and n >= 0:
            return n
        return len(read_include_ids(root))
    path = root / JSONL_NAME
    if not path.exists():
        return 0
    n = 0
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                n += 1
    return n


def archive_raw(root: Path, upload_path: Path, original_filename: str) -> str:
    ensure_package_dirs(root)
    raw = root / RAW_DIR
    suf = Path(original_filename).suffix or upload_path.suffix or ".bin"
    dest = raw / f"original_{uuid.uuid4().hex[:12]}{suf}"
    shutil.copy2(upload_path, dest)
    return str(dest.relative_to(root)).replace("\\", "/")


def package_exists(dataset_id: int) -> bool:
    root = dataset_root(dataset_id)
    return (
        (root / JSONL_NAME).exists()
        or (root / INCLUDE_IDS_NAME).exists()
        or (root / MANIFEST_NAME).exists()
    )


def delete_package(dataset_id: int) -> None:
    root = dataset_root(dataset_id)
    if root.exists() and root.is_dir():
        shutil.rmtree(root, ignore_errors=True)


def csv_bytes_from_package(root: Path) -> bytes:
    csv_path = root / CSV_NAME
    if csv_path.exists():
        return csv_path.read_bytes()
    # 从 jsonl 现场生成
    records = load_records(root)
    buf: list[str] = []
    has_id = any(r.get("id") not in (None, "") for r in records)
    if has_id:
        buf.append("id,text")
        for r in records:
            rid = str(r.get("id") or "").replace('"', '""')
            text = str(r.get("text") or "").replace('"', '""')
            buf.append(f'"{rid}","{text}"')
    else:
        buf.append("text")
        for r in records:
            text = str(r.get("text") or "").replace('"', '""')
            buf.append(f'"{text}"')
    return ("\n".join(buf) + "\n").encode("utf-8-sig")


def preview_records(root: Path, n: int = 20) -> list[dict[str, Any]]:
    rows = load_records(root, limit=n)
    out: list[dict[str, Any]] = []
    for r in rows:
        item: dict[str, Any] = {}
        if r.get("id") is not None:
            item["id"] = r.get("id")
        item["text"] = r.get("text")
        if r.get("uri"):
            item["uri"] = r.get("uri")
        if r.get("modality") and r.get("modality") != "text":
            item["modality"] = r.get("modality")
        out.append(item)
    return out
