"""受管索引的真实适配器；复用现有客户端，不依赖 Service 或 Repository。"""

import hashlib
import math
import os
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit, urlunsplit

import httpx
from openai import APITimeoutError

from backend.rag import embedding_client, vector_store
from backend.rag.index_contracts import EmbeddingBatch, IndexExecutionContext, IndexExecutionError


def _fingerprint(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _embedding_profile() -> dict[str, Any]:
    config = embedding_client.settings
    provider = config.embedding_provider
    if provider not in ("local", "openai"):
        raise IndexExecutionError("index_configuration_changed")
    endpoint = None
    if provider == "openai":
        if bool(config.embedding_base_url) != bool(config.embedding_api_key):
            raise IndexExecutionError("index_configuration_changed")
        url = config.embedding_base_url or config.base_url
        if not url:
            raise IndexExecutionError("index_configuration_changed")
        parsed = urlsplit(url)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            raise IndexExecutionError("index_configuration_changed")
        # 持久化身份只使用无凭据的 origin/path，不保存 userinfo、查询串或 fragment。
        host = parsed.hostname.lower()
        if ":" in host:
            host = f"[{host}]"
        origin = host + (f":{parsed.port}" if parsed.port is not None else "")
        endpoint = _fingerprint(urlunsplit((parsed.scheme.lower(), origin, parsed.path.rstrip("/"), "", "")))
    return {"provider": provider, "model": config.embedding_model,
            "endpoint_fingerprint": endpoint, "normalize_embeddings": provider == "local",
            "embedding_dimension": None}


def _store_fingerprint() -> str:
    path = Path(vector_store.settings.vector_store_dir)
    if not path.is_absolute():
        path = vector_store.PROJECT_ROOT / path
    # 只规范路径，不创建目录或打开现有向量库。
    return _fingerprint(os.path.normcase(str(path.resolve())))


def build_index_target_profile() -> dict[str, Any]:
    """构造初始目标；维度和 metric 留给执行时核实，不连接模型或 collection。"""
    return dict(_embedding_profile(), profile_schema_version=2,
                collection_name=vector_store.COLLECTION_NAME, vector_store_fingerprint=_store_fingerprint(),
                distance_metric=None)


def _check_target(target: dict[str, Any], actual: dict[str, Any]) -> None:
    for key, value in actual.items():
        if target[key] is not None and (type(value) is not type(target[key]) or value != target[key]):
            raise IndexExecutionError("index_configuration_changed")


def _dimension(vectors, expected_count: int) -> int:
    if len(vectors) != expected_count or not vectors:
        raise IndexExecutionError("embedding_count_mismatch")
    dimension = len(vectors[0])
    if dimension <= 0 or any(
        len(vector) != dimension or any(
            isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value)
            for value in vector
        ) for vector in vectors
    ):
        raise IndexExecutionError("embedding_failed")
    return dimension


class ExistingEmbeddingAdapter:
    """复用已有 provider/客户端和批处理；索引调用禁止临时下载或 SDK 隐式重试。"""

    def embed(
        self, texts: tuple[str, ...], *, target_profile: dict[str, Any], context: IndexExecutionContext,
    ) -> EmbeddingBatch:
        context.check_budget()
        if not texts or any(not isinstance(text, str) or not text.strip() for text in texts):
            raise IndexExecutionError("embedding_failed")
        actual = _embedding_profile()
        _check_target(target_profile, {key: value for key, value in actual.items() if key != "embedding_dimension"})

        def client_options():
            context.check_budget()
            remaining = context.remaining_seconds()
            return {"timeout": httpx.Timeout(connect=min(5, remaining), read=min(30, remaining),
                                             write=min(10, remaining), pool=min(5, remaining)),
                    "max_retries": 0}

        try:
            vectors = embedding_client.generate_embeddings(
                list(texts), local_files_only=True, before_batch=context.check_budget, client_options=client_options,
            )
        except (TimeoutError, httpx.TimeoutException, APITimeoutError) as exc:
            raise IndexExecutionError("embedding_timeout") from exc
        actual["embedding_dimension"] = _dimension(vectors, len(texts))
        _check_target(target_profile, actual)
        current = _embedding_profile()
        if any(actual[key] != value for key, value in current.items() if key != "embedding_dimension"):
            raise IndexExecutionError("index_configuration_changed")
        return EmbeddingBatch(vectors, actual)


def _collection_metric(collection) -> str:
    values = []
    config = getattr(collection, "configuration_json", None)
    if isinstance(config, dict):
        for name in ("hnsw", "spann", "hnsw_configuration"):
            section = config.get(name)
            if isinstance(section, dict) and "space" in section:
                values.append(section["space"])
    metadata = getattr(collection, "metadata", None)
    if isinstance(metadata, dict) and "hnsw:space" in metadata:
        values.append(metadata["hnsw:space"])
    if not values or any(value not in ("l2", "cosine", "ip") for value in values):
        raise IndexExecutionError("collection_profile_unverified")
    if len(set(values)) != 1:
        raise IndexExecutionError("index_configuration_changed")
    return values[0]


class ChromaVectorStoreAdapter:
    """只读取现有 collection；不创建、删除、改 metric 或重建任何索引。"""

    def __init__(self, collection_factory: Callable[[], Any] | None = None):
        self.collection_factory = collection_factory

    def _inspect(self, target_profile, context):
        context.check_budget()
        collection = (self.collection_factory() if self.collection_factory is not None else
                      vector_store.get_chroma_collection(create_if_missing=False))
        actual = {"collection_name": collection.name, "vector_store_fingerprint": _store_fingerprint(),
                  "distance_metric": _collection_metric(collection)}
        _check_target(target_profile, actual)
        if collection.name != vector_store.COLLECTION_NAME:
            raise IndexExecutionError("index_configuration_changed")
        dimension = collection.get_model().dimension
        if dimension is not None and (type(dimension) is not int or dimension <= 0):
            raise IndexExecutionError("collection_profile_unverified")
        if dimension is None and collection.count() != 0:
            raise IndexExecutionError("collection_profile_unverified")
        expected = target_profile["embedding_dimension"]
        if dimension is not None and expected is not None and dimension != expected:
            raise IndexExecutionError("index_configuration_changed")
        return collection, actual, dimension

    def inspect_profile(self, *, target_profile: dict[str, Any], context: IndexExecutionContext) -> dict[str, Any]:
        _, actual, _ = self._inspect(target_profile, context)
        return actual

    def upsert(self, *, chunks, embeddings, target_profile: dict[str, Any], context: IndexExecutionContext) -> None:
        collection, actual, collection_dimension = self._inspect(target_profile, context)
        dimension = _dimension(embeddings, len(chunks))
        if (target_profile["embedding_dimension"] != dimension
                or target_profile["distance_metric"] != actual["distance_metric"]
                or collection_dimension is not None and dimension != collection_dimension):
            raise IndexExecutionError("index_configuration_changed")
        if (any(chunk["document_id"] != context.document_id for chunk in chunks)
                or len({chunk["db_chunk_id"] for chunk in chunks}) != len(chunks)):
            raise IndexExecutionError("index_configuration_changed")
        context.check_budget()
        vector_store.upsert_precomputed_document_chunks(
            context.storage_session_id, list(chunks), [list(vector) for vector in embeddings], collection=collection,
        )
