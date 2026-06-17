# -*- coding: utf-8 -*-
"""LanceDB vector backend implementation.

Uses sklearn TfidfVectorizer for lightweight, zero-dependency embeddings.
Falls back to random projections if sklearn unavailable.
"""

from __future__ import annotations

import hashlib
import pickle
import threading
from pathlib import Path

import lancedb
import numpy as np

from ..config import get_config
from . import BaseVectorStore, QueryResult, register_backend


EMBEDDING_DIM = 256
DB_DIR = Path(get_config().data_dir) / "lancedb_data"
VEC_CACHE_PATH = DB_DIR / "vectorizer.pkl"

_vec_cache: dict = {}


def _is_dataset_exists_error(exc: Exception) -> bool:
    return "already exists" in str(exc).lower()


def _get_vectorizer():
    if "vectorizer" in _vec_cache:
        return _vec_cache["vectorizer"]
    try:
        from sklearn.feature_extraction.text import TfidfVectorizer
        if VEC_CACHE_PATH.exists():
            with open(VEC_CACHE_PATH, "rb") as f:
                vec = pickle.load(f)
        else:
            vec = TfidfVectorizer(
                max_features=EMBEDDING_DIM, analyzer="char_wb",
                ngram_range=(2, 4), lowercase=True,
            )
            vec.fit([
                "architecture decision api contract database schema frontend backend bug task deployment coding rule dependency integration",
                "create read update delete search memory project candidate review compact prune deprecated",
                "import export config environment variable secret key token authentication authorization",
            ])
        _vec_cache["vectorizer"] = vec
        return vec
    except ImportError:
        return None


def _save_vectorizer():
    vec = _vec_cache.get("vectorizer")
    if vec is not None:
        VEC_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(VEC_CACHE_PATH, "wb") as f:
            pickle.dump(vec, f)


def _embed_sklearn(texts: list[str]) -> np.ndarray:
    vec = _get_vectorizer()
    if vec is None:
        return _embed_random(texts)
    try:
        result = vec.transform(texts).toarray().astype(np.float32)
    except Exception:
        vec.fit(texts)
        result = vec.transform(texts).toarray().astype(np.float32)
        _save_vectorizer()
    norms = np.linalg.norm(result, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return result / norms


def _embed_random(texts: list[str]) -> np.ndarray:
    dim = EMBEDDING_DIM
    result = np.zeros((len(texts), dim), dtype=np.float32)
    for i, text in enumerate(texts):
        seed = int(hashlib.md5(text.encode("utf-8")).hexdigest()[:8], 16)
        rng = np.random.RandomState(seed)
        vec = rng.randn(dim).astype(np.float32)
        result[i] = vec / (np.linalg.norm(vec) + 1e-8)
    return result


def embed_texts(texts: list[str]) -> np.ndarray:
    return _embed_sklearn(texts)


def embed_query(query: str) -> np.ndarray:
    return embed_texts([query])[0]


def cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.dot(a, b))


class LanceDBStore(BaseVectorStore):
    backend_name = "lancedb"

    def __init__(self, db_path: str | None = None) -> None:
        self._lock = threading.RLock()
        self.db_path = db_path or str(DB_DIR)
        self.db_path_obj = Path(self.db_path)
        self.db_path_obj.mkdir(parents=True, exist_ok=True)
        self.db = lancedb.connect(self.db_path)
        self._ensure_table()

    def _ensure_table(self) -> None:
        try:
            self._get_table()
            return
        except Exception:
            pass

        dummy_vec = embed_texts(["initialize"])[0].tolist()
        try:
            self.db.create_table(
                "memories",
                [{"id": "init", "project_id": "init", "vector": dummy_vec, "text": "init"}],
            )
        except Exception as exc:
            if not _is_dataset_exists_error(exc):
                raise

        tbl = self._get_table()
        tbl.delete("id = 'init'")

    def _get_table(self):
        return self.db.open_table("memories")

    def insert(self, memory_id: str, project_id: str, text: str,
               metadata: dict | None = None) -> None:
        with self._lock:
            vec = embed_texts([text])[0].tolist()
            tbl = self._get_table()
            tbl.add([{"id": memory_id, "project_id": project_id, "vector": vec, "text": text[:1024]}])

    def insert_batch(self, items: list[dict]) -> None:
        if not items:
            return
        with self._lock:
            texts = [item["text"] for item in items]
            vecs = embed_texts(texts)
            rows = [
                {"id": item["id"], "project_id": item["project_id"],
                 "vector": vec.tolist(), "text": item["text"][:1024]}
                for item, vec in zip(items, vecs)
            ]
            self._get_table().add(rows)

    def search(self, query: str, project_id: str | None = None,
               limit: int = 10) -> list[QueryResult]:
        with self._lock:
            query_vec = embed_texts([query])[0].tolist()
            tbl = self._get_table()
            if project_id:
                safe_project_id = project_id.replace("'", "''")
                raw = (
                    tbl.search(query_vec)
                    .where(f"project_id = '{safe_project_id}'", prefilter=True)
                    .limit(limit)
                    .to_list()
                )
            else:
                raw = tbl.search(query_vec).limit(limit).to_list()

            qv = np.array(query_vec, dtype=np.float32)
            results = []
            for r in raw:
                sim = float(np.dot(qv, np.array(r.get("vector", [0]*EMBEDDING_DIM), dtype=np.float32)))
                results.append(QueryResult(
                    id=r["id"], text=r.get("text", ""), similarity=sim,
                    metadata={"project_id": r.get("project_id", "")},
                ))
            _save_vectorizer()
            results.sort(key=lambda x: x.similarity, reverse=True)
            return results

    def delete(self, memory_id: str) -> None:
        with self._lock:
            self._get_table().delete(f"id = '{memory_id}'")

    def delete_by_project(self, project_id: str) -> None:
        with self._lock:
            self._get_table().delete(f"project_id = '{project_id}'")

    def update(self, memory_id: str, project_id: str, text: str,
               metadata: dict | None = None) -> None:
        with self._lock:
            self.delete(memory_id)
            self.insert(memory_id, project_id, text, metadata)

    def count(self) -> int:
        with self._lock:
            tbl = self._get_table()
            if hasattr(tbl, "count_rows"):
                return tbl.count_rows()
            if hasattr(tbl, "to_arrow"):
                return len(tbl.to_arrow())
            return len(tbl.to_pandas())


# Auto-register
register_backend("lancedb", LanceDBStore)
