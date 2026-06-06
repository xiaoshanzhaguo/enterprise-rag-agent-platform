"""
RAG 服务层模块。

职责：
1. 封装当前会话下的 RAG 检索流程，根据用户问题获取最相关的文档片段
2. 将检索结果转换为两种输出形式：
   - 给大模型使用的上下文字符串
   - 给前端展示的片段预览数据
3. 解耦存储层、检索层与业务层，提升 RAG 链路的可维护性

说明：
- 当前实现为第一阶段轻量版 RAG
- 采用“单会话 + 临时索引 + 检索结果组装”的设计
- 适合本地开发、项目演示和求职场景下的工程化展示
"""
from typing import Any

from backend.config import settings
from backend.db.repository import save_rag_query_with_hits
from backend.rag.retriever import retrieve_top_chunks
from backend.rag.store import get_document_chunks


def retrieve_rag_chunks(session_id: str | None, query: str, top_k: int = 3) -> list[dict[str, Any]]:
    """
    根据 session_id 和 query 获取最相关的 RAG 文本块。
    :param session_id: 当前会话 ID，可以是字符串，也可以是 None
    :param query: 当前用户问题
    :param top_k: 最多返回几个最相关的块，默认 3
    :return: 一个列表。列表里的每个元素是一个 chunk 字典。
    """
    # 如果当前没有 session_id，则无法定位到对应会话的文档
    if not session_id:
        return []

    # 先从内存 store 中取出当前会话已索引的文本块
    chunks = get_document_chunks(session_id)
    if not chunks:
        return []

    # 再根据 query 从所有文本块中检索最相关的前 top_k 个
    return retrieve_top_chunks(query=query, chunks=chunks, top_k=top_k)


def build_rag_context_from_chunks(matched_chunks: list[dict[str, Any]]) -> str:
    """
    将已检索出的文本块转换为可直接拼入 prompt 的上下文。
    :param matched_chunks: 检索出的 chunk 列表
    :return: 一个字符串
    """
    # 如果没有命中任何文本块，则返回空字符串
    if not matched_chunks:
        return ""

    # 将每个文本块拼接成带编号、chunk_id 和 score 的参考片段文本
    return "\n\n".join(
        [
            f"[参考片段 {index + 1} | chunk_id={item.get('chunk_id')} | score={item.get('score', 0)}]\n{item['text']}"
            for index, item in enumerate(matched_chunks)
        ]
    )


def build_rag_context(session_id: str | None, query: str, top_k: int = 3) -> str:
    """
    根据 session_id 和 query 构造可直接拼接进 prompt 的检索上下文。
    """
    # 先检索最相关的文本块
    matched_chunks = retrieve_rag_chunks(
        session_id=session_id,
        query=query,
        top_k=top_k
    )

    # 持久化记录本次 RAG 查询及其命中的文本块，便于后续追踪检索效果和调试
    save_rag_query_with_hits(
        session_id=session_id,
        query_text=query,
        top_k=top_k,
        matched_chunks=matched_chunks
    )

    # 再把检索结果转换成 prompt 可直接使用的上下文字符串
    return build_rag_context_from_chunks(matched_chunks)


def build_rag_preview(session_id: str | None, query: str, top_k: int = 3) -> list[dict[str, Any]]:
    """
    构造给前端展示的检索片段摘要。
    """
    # 先检索最相关的文本块
    matched_chunks = retrieve_rag_chunks(
        session_id=session_id,
        query=query,
        top_k=top_k
    )

    # 前端预览文本长度限制，至少保留 80 个字符
    preview_limit = max(settings.rag_preview_text_limit, 80)

    # 返回适合前端展示的摘要结构：
    # - chunk_id：文本块编号
    # - score：相关性分数
    # - text_preview：正文预览, 只截前 preview_limit 个字符
    # - text_length：原始文本总长度
    return [
        {
            "chunk_id": item.get("chunk_id"),
            "score": item.get("score", 0),
            "text_preview": item.get("text", "")[:preview_limit],
            "text_length": len(item.get("text", ""))
        }
        for item in matched_chunks
    ]
