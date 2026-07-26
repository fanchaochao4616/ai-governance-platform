"""数据集向量检索。

两种向量索引（可并存）:

1. **快速向量（TF-IDF）** ``vectors/tfidf/``
   - 字符 n-gram TF-IDF + 余弦
   - 离线、瞬时可建，偏字面相关
   - 也叫 fast vector / lexical vector

2. **语义向量（中文 Embedding 模型）** ``vectors/model/``
   - 默认 ``BAAI/bge-small-zh-v1.5``（中文检索友好）
   - sentence-transformers 本地推理，首次自动下载
   - 余弦相似度

可选 api：OpenAI 兼容 embeddings。
"""

from __future__ import annotations

import json
import math
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

from app.services.dataset_store import VECTORS_DIR, iter_jsonl
from config import (
    EMBEDDING_API_KEY,
    EMBEDDING_BASE_URL,
    EMBEDDING_DIM,
    EMBEDDING_MODEL,
    EMBEDDING_PROVIDER,
    LOCAL_EMBEDDING_MODEL,
)

_TOKEN_RE = re.compile(r"[\w\u4e00-\u9fff]+", re.UNICODE)
_MAX_VOCAB = 12288

# sentence-transformers 模型缓存
_st_model = None
_st_model_name: str | None = None


def _vectors_dir(root: Path, kind: str = "tfidf") -> Path:
    kind = (kind or "tfidf").strip().lower()
    if kind in {"fast", "local", "tfidf", "lexical"}:
        sub = "tfidf"
    elif kind in {"model", "semantic", "st", "bge", "embedding"}:
        sub = "model"
    elif kind in {"api", "cloud"}:
        sub = "api"
    else:
        sub = kind
    d = root / VECTORS_DIR / sub
    d.mkdir(parents=True, exist_ok=True)
    return d


def _normalize_text(text: str) -> str:
    t = (text or "").strip().lower()
    t = re.sub(r"[^\w\u4e00-\u9fff]+", " ", t, flags=re.UNICODE)
    return re.sub(r"\s+", " ", t).strip()


def _features(text: str) -> list[str]:
    t = _normalize_text(text)
    if not t:
        return []
    feats: list[str] = []
    for tok in _TOKEN_RE.findall(t):
        if len(tok) >= 1:
            feats.append(f"w:{tok}")
    compact = t.replace(" ", "")
    for ch in compact:
        feats.append(f"c1:{ch}")
    for n in (2, 3):
        if len(compact) >= n:
            for i in range(len(compact) - n + 1):
                feats.append(f"c{n}:{compact[i : i + n]}")
    return feats


def _build_tfidf(
    texts: list[str],
) -> tuple[np.ndarray, dict[str, Any], dict[str, Any]]:
    doc_feats: list[Counter[str]] = [Counter(_features(t)) for t in texts]
    n_docs = len(doc_feats)
    df: Counter[str] = Counter()
    for c in doc_feats:
        for f in c:
            df[f] += 1
    items = sorted(df.items(), key=lambda x: (-x[1], x[0]))
    if len(items) > _MAX_VOCAB:
        items = items[:_MAX_VOCAB]
    vocab = {f: i for i, (f, _) in enumerate(items)}
    idf = {f: math.log((n_docs + 1.0) / (df[f] + 1.0)) + 1.0 for f in vocab}
    dim = max(len(vocab), 1)
    mat = np.zeros((n_docs, dim), dtype=np.float32)
    for i, c in enumerate(doc_feats):
        if not c:
            continue
        for f, tf in c.items():
            j = vocab.get(f)
            if j is None:
                continue
            mat[i, j] = (1.0 + math.log(tf)) * idf[f]
        norm = float(np.linalg.norm(mat[i]))
        if norm > 1e-12:
            mat[i] /= norm
    meta = {
        "provider": "tfidf",
        "kind": "tfidf",
        "label": "快速向量(TF-IDF)",
        "model": "char-ngram-tfidf",
        "dim": dim,
        "vocab_size": dim,
        "n_docs": n_docs,
    }
    vocab_payload = {"vocab": vocab, "idf": idf, "dim": dim}
    return mat, meta, vocab_payload


def _embed_query_tfidf(query: str, vocab_payload: dict[str, Any]) -> np.ndarray:
    vocab: dict[str, int] = vocab_payload["vocab"]
    idf: dict[str, float] = vocab_payload["idf"]
    dim = int(vocab_payload.get("dim") or len(vocab) or 1)
    vec = np.zeros(dim, dtype=np.float32)
    c = Counter(_features(query))
    for f, tf in c.items():
        j = vocab.get(f)
        if j is None:
            continue
        vec[j] = (1.0 + math.log(tf)) * float(idf.get(f, 1.0))
    norm = float(np.linalg.norm(vec))
    if norm > 1e-12:
        vec /= norm
    return vec


def _get_st_model(model_name: str | None = None):
    """加载中文 sentence-transformers 模型（全局缓存）。"""
    global _st_model, _st_model_name
    name = (model_name or LOCAL_EMBEDDING_MODEL or "BAAI/bge-small-zh-v1.5").strip()
    if _st_model is not None and _st_model_name == name:
        return _st_model, name
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError as e:
        raise RuntimeError(
            "未安装 sentence-transformers。请执行: pip install sentence-transformers"
        ) from e
    # 首次会从 HuggingFace 下载；可用 HF_ENDPOINT 镜像加速
    _st_model = SentenceTransformer(name)
    _st_model_name = name
    return _st_model, name


def _embed_batch_model(texts: list[str], model_name: str | None = None) -> tuple[np.ndarray, str]:
    model, name = _get_st_model(model_name)
    # BGE 系列建议 query 前缀；文档可不加。统一用 encode 的 normalize
    cleaned = [t if str(t).strip() else " " for t in texts]
    emb = model.encode(
        cleaned,
        batch_size=32,
        show_progress_bar=len(cleaned) > 64,
        convert_to_numpy=True,
        normalize_embeddings=True,
    )
    arr = np.asarray(emb, dtype=np.float32)
    if arr.ndim == 1:
        arr = arr.reshape(1, -1)
    return arr, name


def _embed_batch_api(texts: list[str]) -> np.ndarray:
    import httpx
    from openai import OpenAI

    if not EMBEDDING_API_KEY:
        raise RuntimeError("EMBEDDING_API_KEY 未配置")
    http_client = httpx.Client(trust_env=True, timeout=httpx.Timeout(120.0))
    client = OpenAI(
        api_key=EMBEDDING_API_KEY,
        base_url=EMBEDDING_BASE_URL,
        http_client=http_client,
    )
    out: list[list[float]] = []
    batch_size = 64
    for i in range(0, len(texts), batch_size):
        chunk = texts[i : i + batch_size]
        payload = [c if str(c).strip() else " " for c in chunk]
        resp = client.embeddings.create(model=EMBEDDING_MODEL, input=payload)
        data = sorted(resp.data, key=lambda x: x.index)
        out.extend([list(d.embedding) for d in data])
    arr = np.asarray(out, dtype=np.float32)
    norms = np.linalg.norm(arr, axis=1, keepdims=True)
    norms = np.maximum(norms, 1e-12)
    return arr / norms


def _collect_texts(root: Path) -> tuple[list[str], list[dict[str, Any]]]:
    texts: list[str] = []
    keys: list[dict[str, Any]] = []
    for i, rec in enumerate(iter_jsonl(root)):
        text = str(rec.get("text") or "").strip()
        if not text:
            text = " ".join(
                str(x)
                for x in (rec.get("id"), rec.get("uri"), rec.get("modality"))
                if x
            )
        texts.append(text)
        keys.append(
            {
                "row": i,
                "id": rec.get("id"),
                "seq": i + 1,
                "modality": rec.get("modality") or "text",
            }
        )
    return texts, keys


def build_index(root: Path, kind: str = "model") -> dict[str, Any]:
    """构建向量索引。

    kind:
      - model / semantic: 中文 embedding 模型（默认 BAAI/bge-small-zh-v1.5）
      - tfidf / fast: 仅 TF-IDF（内部可选，不对外暴露）
      - api: 云端 embedding
    """
    texts, keys = _collect_texts(root)
    if not texts:
        raise ValueError("数据集为空，无法建向量索引")

    kind = (kind or "model").strip().lower()
    if kind in {"local", "default", "both", "all"}:
        # 对外默认语义模型
        p = (EMBEDDING_PROVIDER or "model").strip().lower()
        if p in {"api", "openai", "cloud"}:
            kind = "api"
        else:
            kind = "model"

    results: dict[str, Any] = {"built": []}

    def _write(
        sub: str,
        matrix: np.ndarray,
        emb_meta: dict[str, Any],
        vocab_payload: dict[str, Any] | None = None,
        *,
        literal_boost: bool = False,
    ) -> dict[str, Any]:
        vdir = _vectors_dir(root, sub)
        np.save(vdir / "matrix.npy", matrix)
        (vdir / "keys.json").write_text(
            json.dumps(keys, ensure_ascii=False), encoding="utf-8"
        )
        (vdir / "texts.json").write_text(
            json.dumps(texts, ensure_ascii=False), encoding="utf-8"
        )
        if vocab_payload is not None:
            (vdir / "vocab.json").write_text(
                json.dumps(vocab_payload, ensure_ascii=False), encoding="utf-8"
            )
        elif (vdir / "vocab.json").exists():
            try:
                (vdir / "vocab.json").unlink()
            except OSError:
                pass
        cfg = {
            **emb_meta,
            "n": int(matrix.shape[0]),
            "built_at": datetime.now(timezone.utc).isoformat(),
            "matrix_file": "matrix.npy",
            "keys_file": "keys.json",
            "texts_file": "texts.json",
            "vocab_file": "vocab.json" if vocab_payload is not None else None,
            "literal_boost": literal_boost,
        }
        (vdir / "config.json").write_text(
            json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        results["built"].append(sub)
        results[sub] = cfg
        return cfg

    # 快速向量 TF-IDF
    if kind in {"tfidf", "fast", "both", "all"}:
        mat, meta, vocab_payload = _build_tfidf(texts)
        _write("tfidf", mat, meta, vocab_payload, literal_boost=True)

    # 中文语义模型
    if kind in {"model", "semantic", "st", "bge", "both", "all"}:
        mat, model_name = _embed_batch_model(texts)
        meta = {
            "provider": "model",
            "kind": "model",
            "label": "语义向量(中文Embedding)",
            "model": model_name,
            "dim": int(mat.shape[1]),
        }
        _write("model", mat, meta, None, literal_boost=False)

    # 云端 API
    if kind in {"api", "cloud"}:
        mat = _embed_batch_api(texts)
        meta = {
            "provider": "api",
            "kind": "api",
            "label": "语义向量(API Embedding)",
            "model": EMBEDDING_MODEL,
            "dim": int(mat.shape[1]),
            "base_url": EMBEDDING_BASE_URL,
        }
        _write("api", mat, meta, None, literal_boost=False)

    if not results["built"]:
        raise ValueError(f"未知索引类型: {kind}")

    # 兼容旧路径：把主索引（优先 model）同步摘要到 vectors/config.json
    primary = (
        results.get("model")
        or results.get("api")
        or results.get("tfidf")
        or {}
    )
    summary = {
        "primary": "model" if "model" in results else (
            "api" if "api" in results else "tfidf"
        ),
        "built": results["built"],
        "model": primary.get("model"),
        "dim": primary.get("dim"),
        "n": primary.get("n"),
        "provider": primary.get("provider"),
        "label": primary.get("label"),
        "built_at": datetime.now(timezone.utc).isoformat(),
    }
    root_v = root / VECTORS_DIR
    root_v.mkdir(parents=True, exist_ok=True)
    (root_v / "config.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    results["summary"] = summary
    # 给上层 rebuild 用的主配置
    results["model_name"] = summary.get("model")
    results["dim"] = summary.get("dim")
    results["n"] = summary.get("n")
    results["provider"] = summary.get("provider")
    results["label"] = summary.get("label")
    return results


def index_status(root: Path, kind: str | None = None) -> dict[str, Any]:
    """查询索引状态。kind 为空时返回汇总。"""
    root_v = root / VECTORS_DIR
    if kind:
        cfg_path = _vectors_dir(root, kind) / "config.json"
        npy = _vectors_dir(root, kind) / "matrix.npy"
        if not cfg_path.exists() or not npy.exists():
            return {"ready": False, "kind": kind, "n": 0}
        try:
            cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
            return {
                "ready": True,
                "kind": kind,
                "n": int(cfg.get("n") or 0),
                "dim": int(cfg.get("dim") or 0),
                "model": cfg.get("model"),
                "provider": cfg.get("provider"),
                "label": cfg.get("label"),
                "built_at": cfg.get("built_at"),
            }
        except Exception:  # noqa: BLE001
            return {"ready": False, "kind": kind, "n": 0}

    # 汇总
    out: dict[str, Any] = {
        "ready": False,
        "tfidf": index_status(root, "tfidf"),
        "model": index_status(root, "model"),
        "api": index_status(root, "api"),
    }
    summary_path = root_v / "config.json"
    if summary_path.exists():
        try:
            out["summary"] = json.loads(summary_path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            pass
    # ready：任一可用
    out["ready"] = bool(
        out["tfidf"].get("ready")
        or out["model"].get("ready")
        or out["api"].get("ready")
    )
    # 兼容旧字段：优先展示语义模型
    primary = out["model"] if out["model"].get("ready") else (
        out["api"] if out["api"].get("ready") else out["tfidf"]
    )
    out["n"] = primary.get("n") or 0
    out["dim"] = primary.get("dim")
    out["model"] = primary.get("model")
    out["provider"] = primary.get("provider")
    out["label"] = primary.get("label")
    out["built_at"] = primary.get("built_at")
    return out


def _load_index(
    root: Path, kind: str
) -> tuple[np.ndarray, list[dict[str, Any]], dict[str, Any], list[str], dict | None]:
    """加载指定类型索引。

    注意：禁止把「语义 model」静默回退到旧版 TF-IDF 扁平索引，
    否则界面选语义检索却实际跑快速向量，且不报错。
    """
    kind_norm = (kind or "tfidf").strip().lower()
    if kind_norm in {"fast", "local", "lexical", "vector_fast"}:
        kind_norm = "tfidf"
    if kind_norm in {"semantic", "st", "bge", "embedding", "vector"}:
        kind_norm = "model"

    vdir = _vectors_dir(root, kind_norm)
    cfg_path = vdir / "config.json"
    npy_path = vdir / "matrix.npy"
    keys_path = vdir / "keys.json"
    if not cfg_path.exists() or not npy_path.exists() or not keys_path.exists():
        # 仅「快速向量」允许兼容旧版扁平 vectors/（provider=local_tfidf）
        legacy = root / VECTORS_DIR
        legacy_cfg = legacy / "config.json"
        legacy_npy = legacy / "matrix.npy"
        if (
            kind_norm == "tfidf"
            and legacy_cfg.exists()
            and legacy_npy.exists()
            and (legacy / "keys.json").exists()
        ):
            try:
                lcfg = json.loads(legacy_cfg.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                lcfg = {}
            prov = str(lcfg.get("provider") or "").lower()
            if prov in {"", "local_tfidf", "tfidf", "local"}:
                vdir = legacy
                cfg_path = legacy_cfg
                npy_path = legacy_npy
                keys_path = legacy / "keys.json"
            else:
                raise ValueError(
                    f"向量索引不存在（{kind_norm}）。请先「重建向量索引」。"
                )
        else:
            if kind_norm == "model":
                raise ValueError(
                    "语义向量索引不存在。请安装 sentence-transformers 后点击"
                    "「重建向量索引」（将下载中文模型 BAAI/bge-small-zh-v1.5）。"
                )
            raise ValueError(
                f"向量索引不存在（{kind_norm}）。请先「重建向量索引」。"
            )
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    matrix = np.load(npy_path)
    keys = json.loads(keys_path.read_text(encoding="utf-8"))
    texts: list[str] = []
    texts_path = vdir / "texts.json"
    if texts_path.exists():
        texts = json.loads(texts_path.read_text(encoding="utf-8"))
    vocab_payload = None
    vocab_path = vdir / "vocab.json"
    if vocab_path.exists():
        vocab_payload = json.loads(vocab_path.read_text(encoding="utf-8"))
    if matrix.ndim != 2 or len(keys) != matrix.shape[0]:
        raise ValueError("向量索引损坏：维度与 keys 不一致")
    return matrix, keys, cfg, texts, vocab_payload


def _literal_bonus(query: str, text: str) -> float:
    q = _normalize_text(query).replace(" ", "")
    t = _normalize_text(text).replace(" ", "")
    if not q or not t:
        return 0.0
    bonus = 0.0
    if q in t:
        bonus += 0.45
    elif t in q and len(t) >= 4:
        bonus += 0.25
    qs, ts = set(q), set(t)
    if qs and ts:
        j = len(qs & ts) / max(len(qs | ts), 1)
        bonus += 0.25 * j

    def bigrams(s: str) -> set[str]:
        if len(s) < 2:
            return set()
        return {s[i : i + 2] for i in range(len(s) - 1)}

    bq, bt = bigrams(q), bigrams(t)
    if bq and bt:
        bj = len(bq & bt) / max(len(bq), 1)
        bonus += 0.35 * bj
    return float(bonus)


def search(
    root: Path,
    query: str,
    *,
    top_k: int = 10,
    kind: str = "model",
    min_score: float | None = None,
) -> list[dict[str, Any]]:
    """余弦相似度检索。

    kind:
      - tfidf / fast: 快速向量
      - model / semantic: 中文 embedding 模型
      - api: 云端

    min_score:
      - 若给出，只保留 score >= min_score 的结果，再取 Top-K
      - None 表示不按阈值过滤
    """
    q = (query or "").strip()
    if not q:
        raise ValueError("检索 query 不能为空")
    # 清洗等场景可能需要取全量命中（按阈值）；检索 UI 仍会自行限制更小 top_k
    top_k = max(1, min(int(top_k), 10000))
    kind = (kind or "model").strip().lower()
    if kind in {"fast", "local", "lexical", "vector_fast"}:
        kind = "tfidf"
    if kind in {"semantic", "st", "bge", "embedding", "vector"}:
        kind = "model"

    kind_norm = kind
    if kind_norm in {"fast", "local", "lexical", "vector_fast"}:
        kind_norm = "tfidf"
    if kind_norm in {"semantic", "st", "bge", "embedding", "vector"}:
        kind_norm = "model"

    thr: float | None = None
    if min_score is not None and str(min_score).strip() != "":
        thr = float(min_score)

    # 索引不存在：按类型构建；语义失败绝不降级到 TF-IDF
    st = index_status(root, kind_norm)
    if not st.get("ready"):
        try:
            build_index(root, kind=kind_norm)
        except Exception as e:  # noqa: BLE001
            if kind_norm == "model":
                raise RuntimeError(
                    "语义向量(BGE)索引构建失败，未回退 TF-IDF。"
                    "请执行: pip install sentence-transformers torch；"
                    f"模型: {LOCAL_EMBEDDING_MODEL}。原始错误: {e}"
                ) from e
            raise

    matrix, keys, cfg, texts, vocab_payload = _load_index(root, kind_norm)
    provider = (cfg.get("provider") or "").lower()

    # 严格分离：model 只能用 BGE 编码；tfidf 只能用词表编码
    if kind_norm == "model":
        if provider in {"local_tfidf", "tfidf", "local"} or (
            provider != "model" and vocab_payload is not None and provider != "api"
        ):
            raise RuntimeError(
                "语义检索误加载了 TF-IDF 索引。请删除 data/datasets/{id}/vectors "
                "后点「重建语义索引」，确保生成 vectors/model/。"
            )
        try:
            qv, used_name = _embed_batch_model([q], cfg.get("model") or LOCAL_EMBEDDING_MODEL)
            qv = qv[0]
            cfg = {**cfg, "model": used_name, "provider": "model"}
        except Exception as e:  # noqa: BLE001
            raise RuntimeError(
                "BGE 模型不可用，语义检索中止（不回退 TF-IDF）。"
                f" 详情: {e}"
            ) from e
    elif kind_norm == "tfidf":
        if vocab_payload is None:
            raise ValueError("快速向量索引缺少 vocab.json，请重建快速向量索引")
        qv = _embed_query_tfidf(q, vocab_payload)
    elif provider == "api" and EMBEDDING_API_KEY:
        qv = _embed_batch_api([q])[0]
    else:
        raise ValueError(f"无法编码查询向量 kind={kind_norm} provider={provider}")

    cos = matrix @ qv.astype(np.float32)
    scores = cos.copy()
    if texts and cfg.get("literal_boost", False):
        for i, doc in enumerate(texts):
            scores[i] = float(scores[i]) + _literal_bonus(q, doc)

    # 先阈值，再 Top-K（避免「先取 K 再滤」导致结果偏少且不稳定）
    if thr is not None:
        cand = np.flatnonzero(scores >= thr)
        if cand.size == 0:
            return []
        order = cand[np.argsort(-scores[cand])]
        idx = order[: min(top_k, order.shape[0])]
    else:
        k = min(top_k, scores.shape[0])
        if k >= scores.shape[0]:
            idx = np.argsort(-scores)
        else:
            part = np.argpartition(-scores, k)[:k]
            idx = part[np.argsort(-scores[part])]

    from app.services.dataset_store import load_records

    records = load_records(root)
    hits: list[dict[str, Any]] = []
    for i in idx:
        i = int(i)
        key = keys[i] if i < len(keys) else {"row": i}
        rec = records[i] if i < len(records) else {}
        hits.append(
            {
                "rank": len(hits) + 1,
                "score": float(scores[i]),
                "cosine": float(cos[i]),
                "seq": key.get("seq") or (i + 1),
                "id": rec.get("id") if rec.get("id") is not None else key.get("id"),
                "text": rec.get("text"),
                "uri": rec.get("uri"),
                "modality": rec.get("modality") or key.get("modality") or "text",
                "index_kind": kind_norm,
                "index_label": cfg.get("label")
                or (
                    "语义向量(BGE)"
                    if kind_norm == "model"
                    else "快速向量(TF-IDF)"
                ),
                "model": cfg.get("model"),
            }
        )
    return hits
