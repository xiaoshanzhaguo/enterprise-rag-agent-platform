"""
RAG 数据库存储模块。

职责：
1. 将上传文档切分后的文本块保存到 SQLite。
2. 从 SQLite 读取当前会话的 RAG 文本块，用于检索。
3. 从 SQLite 查询当前会话的文档状态，供 /rag_status 展示。
4. 在向量检索模式下，将 chunk 同步写入 ChromaDB。
5. 删除当前会话已持久化的 RAG 文档、文本块和向量记录。

说明：
- 当前模块不再使用进程内存作为 RAG store。
- 上传文档会写入 documents 和 document_chunks。
- 检索时会从 document_chunks 读取文本块，因此后端重启后仍可继续检索。
- 当 RAG_RETRIEVAL_MODE=vector 时，上传文档后会额外生成 embedding 并写入 data/chroma/。
"""

from __future__ import annotations

from typing import Any

from backend.config import settings
from backend.db.repository import delete_session_documents
from backend.db.repository import get_document_chunks as get_persisted_document_chunks
from backend.db.repository import get_document_status as get_persisted_document_status
from backend.db.repository import save_document_with_chunks
from backend.rag.vector_store import delete_session_vectors, upsert_document_chunks


def save_document_chunks(session_id: str, file_name: str | None, chunks: list[str]) -> None:
    """
    将切分后的文本块保存到数据库。

    函数说明：
    1. 接收已经切分好的文本块。
    2. 调用 repository 层保存 documents 和 document_chunks。
    3. 当前函数只负责 RAG 存储层转发，不直接拼 SQL。

    :param session_id: 会话 ID
    :param file_name: 文件名
    :param chunks: 切好的文本块列表
    :return: None
    """
    # 调用数据库仓储层，将文档和 chunk 持久化到 SQLite
    saved_chunks = save_document_with_chunks(
        session_id=session_id,
        file_name=file_name,
        chunks=chunks,
    )

    # 如果启用了向量检索，则把 SQLite 保存后的 chunk 同步写入 ChromaDB
    if settings.rag_retrieval_mode == "vector":
        upsert_document_chunks(
            session_id=session_id,
            chunks=saved_chunks,
        )


def get_document_chunks(session_id: str) -> list[dict[str, Any]]:
    """
    从数据库获取某个 session 当前已索引的文本块列表。

    函数说明：
    1. 接收当前会话 ID。
    2. 调用 repository 层从 document_chunks 读取文本块。
    3. 返回给 RAG 检索服务使用。

    :param session_id: 会话 ID
    :return: 文本块列表
    """
    # 从数据库仓储层读取当前 session 的持久化 chunk
    return get_persisted_document_chunks(session_id)


def get_document_status(session_id: str) -> dict[str, Any]:
    """
    从数据库返回当前 session 的 RAG 文档状态。

    函数说明：
    1. 接收当前会话 ID。
    2. 调用 repository 层查询 documents 和 document_chunks。
    3. 返回 /rag_status 接口需要的状态结构。

    :param session_id: 会话 ID
    :return: RAG 文档状态
    """
    # 从数据库仓储层读取当前 session 的文档状态
    return get_persisted_document_status(session_id)


def clear_document_chunks(session_id: str) -> None:
    """
    删除某个 session 已持久化的 RAG 文档和文本块。

    函数说明：
    1. 接收当前会话 ID。
    2. 如果启用了向量检索，则删除 ChromaDB 中对应的向量。
    3. 调用 repository 层删除 documents。
    4. 依赖数据库外键级联删除 document_chunks。

    :param session_id: 会话 ID
    :return: None
    """
    # 如果启用了向量检索，则先删除当前 session 的向量记录
    if settings.rag_retrieval_mode == "vector":
        delete_session_vectors(session_id)

    # 删除当前 session 的持久化文档和对应 chunk
    delete_session_documents(session_id)
