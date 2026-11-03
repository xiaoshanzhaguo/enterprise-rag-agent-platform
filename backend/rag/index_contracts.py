"""索引执行的中立协议与数据类型；不依赖 Service、SQL 或具体向量客户端。"""

from dataclasses import dataclass, field
from typing import Any, Callable, Protocol


_EXECUTOR_ERRORS = {
    "embedding_failed", "embedding_timeout", "embedding_count_mismatch", "collection_profile_unverified",
    "upsert_failed", "upsert_outcome_unknown", "index_configuration_changed", "attempt_budget_exceeded",
}



class IndexExecutionError(Exception):
    """适配器只传受控代码，不向任务回执传播原始 SDK 异常。"""

    def __init__(self, code: str):
        if code not in _EXECUTOR_ERRORS:
            raise ValueError("不支持的索引执行错误代码。")
        self.code = code
        super().__init__(code)



@dataclass(frozen=True)
class IndexExecutionContext:
    operation_id: str
    document_id: int
    state_version: int
    attempt_token: str
    storage_session_id: str
    deadline: float
    clock: Callable[[], float] = field(repr=False, compare=False)

    def remaining_seconds(self) -> float:
        return max(0.0, self.deadline - self.clock())

    def check_budget(self) -> None:
        if self.remaining_seconds() <= 0:
            raise IndexExecutionError("attempt_budget_exceeded")



@dataclass(frozen=True)
class EmbeddingBatch:
    vectors: list[list[float]]
    # 仅 Embedding 的实际参数；collection / metric 由独立 Store 接口提供。
    profile: dict[str, Any]



class EmbeddingExecutor(Protocol):
    def embed(
        self, texts: tuple[str, ...], *, target_profile: dict[str, Any], context: IndexExecutionContext,
    ) -> EmbeddingBatch:
        """同步返回实际向量及 Embedding 配置，不在返回后继续后台执行。"""
        ...



class VectorStoreExecutor(Protocol):
    def inspect_profile(
        self, *, target_profile: dict[str, Any], context: IndexExecutionContext,
    ) -> dict[str, Any]:
        """返回 collection 名称、存储指纹及实际 metric，不写入向量。"""
        ...

    def upsert(
        self, *, chunks: tuple[dict[str, Any], ...], embeddings: tuple[tuple[float, ...], ...],
        target_profile: dict[str, Any], context: IndexExecutionContext,
    ) -> None:
        """按既有块 ID 与存储作用域写入；ID 映射归适配器，不归 Service。"""
        ...
