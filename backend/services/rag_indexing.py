"""同步索引业务编排；外部能力由接口注入，本模块不操作 SQL 或构造向量 ID。"""

from copy import deepcopy
from dataclasses import dataclass, replace
import math
from threading import BoundedSemaphore
from time import monotonic
from typing import Any, Callable

from backend.db import index_repository as repository
from backend.rag.index_contracts import (
    EmbeddingBatch, EmbeddingExecutor, IndexExecutionContext, IndexExecutionError, VectorStoreExecutor,
)


_EXECUTION_GATE = BoundedSemaphore(1)
_EMBEDDING_FIELDS = {"provider", "model", "endpoint_fingerprint", "normalize_embeddings", "embedding_dimension"}
_STORE_FIELDS = {"collection_name", "vector_store_fingerprint", "distance_metric"}


@dataclass(frozen=True)
class IndexExecutionResult:
    operation_id: str
    operation: dict[str, Any] | None
    execution_started: bool
    state_confirmed: bool
    reason: str
    error_code: str | None = None


class RagIndexingService:
    """仅处理已有任务的 resume/retry；无默认真实执行器、后台队列或 HTTP 入口。"""

    def __init__(
        self, embedding: EmbeddingExecutor, vector_store: VectorStoreExecutor, *, owner_instance_id: str,
        retrieval_mode: str = "vector", budget_seconds: float = 120,
        clock: Callable[[], float] = monotonic, execution_gate: BoundedSemaphore | None = None,
    ):
        if not isinstance(owner_instance_id, str) or not owner_instance_id.strip():
            raise ValueError("执行实例标识不能为空。")
        if retrieval_mode not in ("keyword", "vector"):
            raise ValueError("检索模式不合法。")
        if (isinstance(budget_seconds, bool) or not isinstance(budget_seconds, (int, float))
                or not math.isfinite(budget_seconds) or budget_seconds <= 0):
            raise ValueError("执行预算必须是有限正数。")
        self.embedding = embedding
        self.vector_store = vector_store
        self.owner_instance_id = owner_instance_id
        self.retrieval_mode = retrieval_mode
        self.budget_seconds = budget_seconds
        self.clock = clock
        self.execution_gate = execution_gate if execution_gate is not None else _EXECUTION_GATE

    def resume(self, operation_id: str, expected_state_version: int) -> IndexExecutionResult:
        return self._start(operation_id, expected_state_version, retry=False)

    def retry(self, operation_id: str, expected_state_version: int) -> IndexExecutionResult:
        return self._start(operation_id, expected_state_version, retry=True)

    @staticmethod
    def _result(operation: dict[str, Any], reason: str, started: bool = False) -> IndexExecutionResult:
        return IndexExecutionResult(operation["operation_id"], operation, started, True, reason,
                                    operation["error_code"] if reason == "failed" else None)

    @staticmethod
    def _unconfirmed(operation_id: str, started: bool) -> IndexExecutionResult:
        # 不返回缓存的 ready 或 failed，避免把未提交/未确认的状态冒充持久化事实。
        return IndexExecutionResult(operation_id, None, started, False, "state_unconfirmed",
                                    "state_confirmation_failed")

    def _read(self, operation_id: str) -> dict[str, Any]:
        operation = repository.get_index_operation(operation_id)
        if operation is None:
            raise LookupError("索引任务不存在。")
        return operation

    def _start(self, operation_id: str, expected_version: int, *, retry: bool) -> IndexExecutionResult:
        if not isinstance(operation_id, str) or not operation_id.strip():
            raise ValueError("操作 ID 不能为空。")
        if type(expected_version) is not int or expected_version <= 0:
            raise ValueError("状态版本必须是正整数。")
        try:
            operation = self._read(operation_id)
        except LookupError:
            raise
        except Exception:
            return self._unconfirmed(operation_id, False)
        if operation.get('lifecycle_state', 'active') != 'active':
            return self._result(operation, 'document_deleting')
        if operation["status"] == "ready":
            return self._result(operation, "ready")
        if operation["execution_claimed"]:
            return self._result(operation, "already_claimed")
        if self.retrieval_mode != "vector":
            return self._result(operation, "vector_index_disabled")
        required_status = "failed" if retry else "pending"
        if operation["status"] != required_status:
            return self._result(operation, "resume_required" if operation["status"] == "pending" else
                                "retry_required" if operation["status"] == "failed" else "index_not_requested")
        if operation["state_version"] != expected_version:
            return self._result(operation, "version_conflict")
        if retry:
            try:
                operation = repository.prepare_index_retry(operation_id, expected_version)
            except repository.IndexOperationConflict:
                return self._claim_conflict(operation_id)
            except Exception:
                return self._unconfirmed(operation_id, False)
            if operation["status"] == "ready" or operation["execution_claimed"]:
                return self._result(operation, "ready" if operation["status"] == "ready" else "already_claimed")
        if not self.execution_gate.acquire(blocking=False):
            return self._result(operation, "capacity_unavailable")
        try:
            try:
                claimed = repository.claim_index_operation(
                    operation_id, operation["state_version"], self.owner_instance_id,
                )
            except repository.IndexOperationConflict:
                return self._claim_conflict(operation_id)
            except Exception:
                return self._unconfirmed(operation_id, False)
            return self._execute(claimed)
        finally:
            # 只有同步调用真正结束才释放；预算或请求端超时不启动并行后台任务。
            self.execution_gate.release()

    def _claim_conflict(self, operation_id: str) -> IndexExecutionResult:
        try:
            operation = self._read(operation_id)
        except Exception:
            return self._unconfirmed(operation_id, False)
        return self._result(operation, "ready" if operation["status"] == "ready" else
                            "already_claimed" if operation["execution_claimed"] else "version_conflict")

    def _execute(self, operation: dict[str, Any]) -> IndexExecutionResult:
        context = IndexExecutionContext(
            operation["operation_id"], operation["document_id"], operation["state_version"],
            operation["attempt_token"], operation["storage_session_id"],
            self.clock() + self.budget_seconds, self.clock,
        )
        stage = "input"
        try:
            loaded = repository.load_index_input(context.operation_id)
            current = loaded["operation"]
            if current["attempt_token"] != context.attempt_token or current["state_version"] != context.state_version:
                raise repository.IndexOperationConflict("执行上下文已变化。")
            chunks = loaded["chunks"]
            target = current["target_profile"]
            context.check_budget()
            stage = "store_profile"
            store_profile = self.vector_store.inspect_profile(target_profile=deepcopy(target), context=context)
            if not isinstance(store_profile, dict) or store_profile.get("distance_metric") not in ("l2", "cosine", "ip"):
                raise IndexExecutionError("collection_profile_unverified")
            self._validate_component(store_profile, _STORE_FIELDS, target)
            context.check_budget()
            stage = "embedding"
            batch = self.embedding.embed(tuple(chunk["text"] for chunk in chunks),
                                         target_profile=deepcopy(target), context=context)
            self._validate_component(batch.profile, _EMBEDDING_FIELDS, target)
            embeddings = self._validated_vectors(batch, len(chunks))
            context.check_budget()
            stage = "binding"
            operation = repository.bind_index_profile(
                context.operation_id, context.attempt_token, context.state_version,
                {"embedding_dimension": batch.profile["embedding_dimension"],
                 "distance_metric": store_profile["distance_metric"]},
            )
            context = replace(context, state_version=operation["state_version"])
            context.check_budget()
            stage = "upsert"
            latest = repository.load_index_input(context.operation_id)['operation']
            if latest['attempt_token'] != context.attempt_token or latest['state_version'] != context.state_version:
                raise repository.IndexOperationConflict('索引执行权已变化。')
            self.vector_store.upsert(chunks=tuple(deepcopy(chunks)), embeddings=embeddings,
                                     target_profile=deepcopy(operation["target_profile"]), context=context)
        except Exception as exc:
            code = self._failure_code(stage, exc)
            return self._fail(context, code)
        # upsert 已正常返回：即使超出协作预算也要确认结果，不能谎称没有写入。
        try:
            ready = repository.finish_index_operation(context.operation_id, context.attempt_token, "ready")
        except Exception:
            # 确认可能已提交但回执丢失；不盲目改 failed 或重新 upsert。
            return self._unconfirmed(context.operation_id, True)
        return self._result(ready, "ready", True)

    @staticmethod
    def _validate_component(actual: dict[str, Any], fields: set[str], target: dict[str, Any]) -> None:
        if not isinstance(actual, dict) or set(actual) != fields:
            raise IndexExecutionError("index_configuration_changed")
        for key in fields:
            if key in ("embedding_dimension", "distance_metric") and target[key] is None:
                if actual[key] is None:
                    raise IndexExecutionError("index_configuration_changed")
                continue
            if type(actual[key]) is not type(target[key]) or actual[key] != target[key]:
                raise IndexExecutionError("index_configuration_changed")

    @staticmethod
    def _validated_vectors(batch: EmbeddingBatch, expected_count: int) -> tuple[tuple[float, ...], ...]:
        if len(batch.vectors) != expected_count:
            raise IndexExecutionError("embedding_count_mismatch")
        dimension = batch.profile["embedding_dimension"]
        if type(dimension) is not int or dimension <= 0:
            raise IndexExecutionError("embedding_failed")
        vectors = []
        for vector in batch.vectors:
            if len(vector) != dimension or any(
                isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value)
                for value in vector
            ):
                raise IndexExecutionError("embedding_failed")
            vectors.append(tuple(float(value) for value in vector))
        return tuple(vectors)

    @staticmethod
    def _failure_code(stage: str, exc: Exception) -> str:
        if isinstance(exc, repository.IndexInputChanged):
            return "index_input_changed"
        if isinstance(exc, IndexExecutionError):
            return exc.code
        if isinstance(exc, repository.IndexOperationConflict):
            return "index_configuration_changed"
        if stage == "embedding":
            return "embedding_timeout" if isinstance(exc, TimeoutError) else "embedding_failed"
        return {"store_profile": "collection_profile_unverified", "upsert": "upsert_outcome_unknown"}.get(
            stage, "interrupted",
        )

    def _fail(self, context: IndexExecutionContext, code: str) -> IndexExecutionResult:
        try:
            failed = repository.finish_index_operation(context.operation_id, context.attempt_token, "failed", code)
        except Exception:
            return self._unconfirmed(context.operation_id, True)
        return self._result(failed, "failed", True)


def create_rag_indexing_service(*, owner_instance_id: str, **options) -> RagIndexingService:
    """显式装配真实适配器；仅构造对象，不加载模型或打开 collection。"""
    from backend.config import settings
    from backend.rag.index_adapters import ChromaVectorStoreAdapter, ExistingEmbeddingAdapter

    options.setdefault("retrieval_mode", settings.rag_retrieval_mode)
    return RagIndexingService(ExistingEmbeddingAdapter(), ChromaVectorStoreAdapter(),
                              owner_instance_id=owner_instance_id, **options)
