"""
RAG 内存存储模块。

职责：
1. 以 session_id 为单位，临时保存上传文档切分后的文本块
2. 提供文档块读取、状态查询和手动清理能力
3. 支持过期清理和最大会话数限制，避免内存持续增长

说明：
- 当前为第一阶段实现，采用内存存储
- 不做数据库持久化
- 适合单机、本地开发和项目演示场景
"""
import time
from typing import Any

from backend.config import settings
from backend.db.repository import save_document_with_chunks


# 全局内存级 RAG 存储：
# - key: session_id
# - value: 当前会话对应的文档记录
RAG_STORE: dict[str, dict[str, Any]] = {}


def _now() -> float:
    """
    返回当前时间戳。
    """
    return time.time()


def _is_expired(record: dict[str, Any], current_time: float | None = None) -> bool:
    """
    判断某条 RAG 记录是否已过期。
    :param record: 某个 session 的文档记录
    :param current_time: 当前时间，可选；如果不传就自动取当前时间
    :return: True or False
    """
    checked_at = current_time or _now()  # 如果未传当前时间，则使用系统当前时间
    expires_at = record.get("expires_at", 0)  # 取出记录的过期时间
    return bool(expires_at and expires_at <= checked_at)  # 到达过期时间则返回 True


def clear_expired_document_chunks() -> int:
    """
    清理已过期的内存 RAG 文档，返回清理数量。
    """
    checked_at = _now()

    # 找出所有已经过期的 session_id
    expired_session_ids = [
        session_id
        for session_id, record in RAG_STORE.items()
        if _is_expired(record, checked_at)
    ]

    # 从内存存储中删除过期记录
    for session_id in expired_session_ids:
        RAG_STORE.pop(session_id, None)

    return len(expired_session_ids)


def enforce_store_limit() -> None:
    """
    按最近访问时间做简单 LRU 淘汰，避免内存 store 无限增长。
    """
    max_sessions = max(settings.rag_store_max_sessions, 1) # 最大会话数至少为 1
    # 如果当前存储数量还没超过上限，就不用处理，直接返回。
    if len(RAG_STORE) <= max_sessions:
        return

    # 按“最后访问时间”从旧到新排序
    sorted_items = sorted(
        RAG_STORE.items(),
        key=lambda item: item[1].get("last_accessed_at", item[1].get("created_at", 0))
    )

    # 删除最旧的若干条记录，直到总数不超过上限
    for session_id, _ in sorted_items[:len(RAG_STORE) - max_sessions]:
        RAG_STORE.pop(session_id, None)


def save_document_chunks(session_id: str, file_name: str | None, chunks: list[str]) -> None:
    """
    将切分后的文本块存入当前 session 对应的内存 RAG store。
    :param session_id: 会话 ID
    :param file_name: 文件名
    :param chunks: 切好的文本块列表
    :return: None
    """
    clear_expired_document_chunks() # 保存前先清理过期数据

    created_at = _now()
    ttl_seconds = max(settings.rag_store_ttl_seconds, 60) # 过期时间至少保留 60 秒
    # 将当前文档及其切分后的文本块持久化到数据库，并获取带数据库主键的 chunk 元数据
    saved_chunks = save_document_with_chunks(
        session_id=session_id,
        file_name=file_name,
        chunks=chunks
    )

    # 覆盖写入当前 session 的文档记录
    RAG_STORE[session_id] = {
        "file_name": file_name,
        "created_at": created_at,
        "last_accessed_at": created_at,
        "expires_at": created_at + ttl_seconds,
        # 优先使用数据库保存后返回的 chunk 元数据；如果保存失败或无返回，则退回到内存临时 chunk 结构
        # saved_chunks的作用：保留数据库里的真实 chunk ID
        "chunks": saved_chunks or [
            {
                "chunk_id": index + 1,
                "text": chunk
            }
            for index, chunk in enumerate(chunks)
        ]
    }

    enforce_store_limit() # 保存后检查是否超出最大存储上限


def get_document_record(session_id: str) -> dict[str, Any] | None:
    """
    获取某个 session 的 RAG 记录；若已过期则立即清理。
    找到就返回记录字典，找不到返回 None。
    """
    record = RAG_STORE.get(session_id)
    if not record:
        return None

    # 如果这条记录已经过期，就顺手删掉它，并返回 None。避免前面没清掉的过期数据继续被拿去用。
    if _is_expired(record):
        RAG_STORE.pop(session_id, None)
        return None

    record["last_accessed_at"] = _now() # 更新最近访问时间
    return record


def get_document_chunks(session_id: str) -> list[dict[str, Any]]:
    """
    获取某个 session 当前已索引的文本块列表。
    """
    # 先拿完整记录。如果拿不到（不存在或已过期），就返回空列表。
    record = get_document_record(session_id)
    if not record:
        return []

    return record.get("chunks", [])


def get_document_status(session_id: str) -> dict[str, Any]:
    """
    返回当前 session 的 RAG store 状态，供前端展示和调试。
    """
    record = get_document_record(session_id)
    # 如果当前没有文档记录，就返回一个“空状态”。
    if not record:
        return {
            "session_id": session_id,
            "has_document": False,
            "file_name": None,
            "chunk_count": 0,
            "expires_in_seconds": 0
        }

    # 计算当前记录距离过期还剩多少秒
    expires_in_seconds = max(int(record["expires_at"] - _now()), 0)

    return {
        "session_id": session_id,
        "has_document": True,
        "file_name": record.get("file_name"),
        "chunk_count": len(record.get("chunks", [])),
        "expires_in_seconds": expires_in_seconds
    }


def clear_document_chunks(session_id: str) -> None:
    """
    清理某个 session 已索引的文档。
    """
    RAG_STORE.pop(session_id, None)
