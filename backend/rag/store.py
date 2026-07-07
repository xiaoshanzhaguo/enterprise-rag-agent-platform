"""
RAG 数据库存储模块。

职责：
1. 将上传文档切分后的文本块保存到 SQLite。
2. 从 SQLite 读取当前会话全部 RAG 文本块，用于检索。
3. 从 SQLite 查询当前会话的多文档状态，供 /rag_status 展示。
4. 在向量检索模式下，将 chunk 同步写入 ChromaDB。
5. 删除当前会话已持久化的 RAG 文档、文本块和向量记录。

说明：
- 当前模块不再使用进程内存作为 RAG store。
- 上传文档会追加写入 documents 和 document_chunks，同一会话可以累计多份文档。
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
from backend.rag.knowledge_bases import build_knowledge_base_storage_session_id, normalize_knowledge_base_id
from backend.rag.vector_store import delete_session_vectors, upsert_document_chunks


def save_document_chunks(
    session_id: str,
    file_name: str | None,
    chunks: list[str],
    knowledge_base_id: str | None = None,
    knowledge_base_type: str = "general",
    department: str | None = None,
    process_type: str | None = None,
    process_status: str = "active",
) -> None:
    """
    将切分后的文本块保存到数据库。

    函数说明：
    1. 接收已经切分好的文本块。
    2. 调用 repository 层追加保存 documents 和 document_chunks。
    3. 当前函数只负责 RAG 存储层转发，不直接拼 SQL。

    :param session_id: 会话 ID
    :param file_name: 文件名
    :param chunks: 切好的文本块列表
    :param knowledge_base_id: 文档所属企业知识库 ID
    :param knowledge_base_type: 知识库类型，例如 hr、finance、it、product
    :param department: 所属部门，例如 HR、财务、IT、产品
    :param process_type: 流程类型，例如 leave、reimbursement、vpn
    :param process_status: 流程状态，例如 active、draft、archived
    :return: None
    """
    # 调用数据库仓储层，将文档和 chunk 持久化到 SQLite
    saved_chunks = save_document_with_chunks(
        session_id=session_id,
        file_name=file_name,
        chunks=chunks,
        knowledge_base_id=knowledge_base_id,
        knowledge_base_type=knowledge_base_type,
        department=department,
        process_type=process_type,
        process_status=process_status,
    )

    # 如果启用了向量检索，则把 SQLite 保存后的 chunk 同步写入 ChromaDB
    if settings.rag_retrieval_mode == "vector":
        # 公共知识库向量要挂到内部知识库 session，不能挂到上传者聊天 session。
        # 旧入口没有 knowledge_base_id 时，继续挂到当前 session，保持原有行为。
        vector_session_id = (
            build_knowledge_base_storage_session_id(normalize_knowledge_base_id(knowledge_base_id))
            if knowledge_base_id
            else session_id
        )
        upsert_document_chunks(
            session_id=vector_session_id,
            chunks=saved_chunks,
        )


def get_document_chunks(session_id: str, knowledge_base_id: str | None = None) -> list[dict[str, Any]]:
    """
    从数据库获取某个 session 当前已索引的全部文本块列表。

    函数说明：
    1. 接收当前会话 ID。
    2. 调用 repository 层从 document_chunks 读取当前知识库或当前会话的文本块。
    3. 返回给 RAG 检索服务使用。

    :param session_id: 会话 ID
    :param knowledge_base_id: 企业知识库 ID；传入后优先读取公共知识库文档
    :return: 文本块列表
    """
    # 从数据库仓储层读取当前知识库或旧 session 的持久化 chunk
    return get_persisted_document_chunks(session_id, knowledge_base_id=knowledge_base_id)


def get_document_status(session_id: str, knowledge_base_id: str | None = None) -> dict[str, Any]:
    """
    从数据库返回当前 session 的 RAG 多文档状态。

    函数说明：
    1. 接收当前会话 ID。
    2. 调用 repository 层查询当前知识库或当前会话全部 documents 和 document_chunks。
    3. 返回 /rag_status 接口需要的状态结构。

    :param session_id: 会话 ID
    :param knowledge_base_id: 企业知识库 ID；传入后优先查询公共知识库状态
    :return: RAG 文档状态
    """
    # 从数据库仓储层读取当前知识库或旧 session 的文档状态
    return get_persisted_document_status(session_id, knowledge_base_id=knowledge_base_id)


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
