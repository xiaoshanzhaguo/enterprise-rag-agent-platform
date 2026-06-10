"""
RAG 服务层模块。

职责：
1. 封装当前会话下的 RAG 检索流程，根据用户问题获取最相关的文档片段
2. 将检索结果转换为两种输出形式：
   - 给大模型使用的带引用来源上下文字符串
   - 给前端展示的引用来源和原文片段数据
3. 根据配置在关键词检索和 ChromaDB 向量检索之间切换
4. 解耦存储层、检索层与业务层，提升 RAG 链路的可维护性

说明：
- 当前实现为第一阶段轻量版 RAG
- 采用“单会话 + 数据库持久化 chunks + 可选向量库检索 + 检索结果组装”的设计
- 检索上下文会携带 file_name、chunk_id、score，并要求模型按来源引用回答
- 当 RAG_RETRIEVAL_MODE=vector 时，检索会走 ChromaDB 语义相似度
- 适合本地开发、项目演示和求职场景下的工程化展示
"""
from typing import Any

from backend.config import settings
from backend.db.repository import save_rag_query_with_hits
from backend.rag.retriever import retrieve_top_chunks
from backend.rag.store import get_document_chunks
from backend.rag.vector_store import retrieve_similar_chunks


NO_RAG_EVIDENCE_MESSAGE = "知识库中没有找到依据"


def build_source_label(chunk: dict[str, Any]) -> str:
    """
    根据命中的 chunk 构造统一的引用来源标识。

    函数说明：
    1. 优先使用 chunk 自带的 file_name。
    2. 使用 chunk_id 拼出 #chunk-n 格式。
    3. 返回给 prompt 和前端共同使用的来源字符串。

    :param chunk: 单个 RAG 命中文本块
    :return: 引用来源字符串，例如：员工手册.md#chunk-4
    """
    # 读取文件名；如果数据库里没有文件名，就使用兜底名称
    file_name = chunk.get("file_name") or "当前文档"
    # 读取文本块编号；如果缺失，就使用 unknown，避免引用格式断裂
    chunk_id = chunk.get("chunk_id") or "unknown"
    # 拼成最终引用标识，供模型和前端展示
    return f"{file_name}#chunk-{chunk_id}"


def retrieve_rag_chunks(session_id: str | None, query: str, top_k: int = 3) -> list[dict[str, Any]]:
    """
    根据 session_id 和 query 获取最相关的 RAG 文本块。

    函数说明：
    1. 没有 session_id 时直接返回空列表。
    2. RAG_RETRIEVAL_MODE=vector 时，使用 ChromaDB 向量检索。
    3. RAG_RETRIEVAL_MODE=keyword 时，使用当前轻量关键词检索。
    4. 返回统一 chunk 字典结构，供 prompt 和前端引用预览复用。

    :param session_id: 当前会话 ID，可以是字符串，也可以是 None
    :param query: 当前用户问题
    :param top_k: 最多返回几个最相关的块，默认 3
    :return: 一个列表。列表里的每个元素是一个 chunk 字典。
    """
    # 如果当前没有 session_id，则无法定位到对应会话的文档
    if not session_id:
        return []

    # 如果配置为向量模式，则直接通过 ChromaDB 按语义相似度检索
    if settings.rag_retrieval_mode == "vector":
        return retrieve_similar_chunks(
            session_id=session_id,
            query=query,
            top_k=top_k
        )

    # 先从数据库中取出当前会话已索引的文本块
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
    # 如果没有命中任何文本块，则返回明确的无依据提示，避免模型绕开知识库凭空回答
    if not matched_chunks:
        return (
            "【检索结果】\n"
            f"{NO_RAG_EVIDENCE_MESSAGE}。\n\n"
            "回答要求：\n"
            f"1. 请明确回复“{NO_RAG_EVIDENCE_MESSAGE}”。\n"
            "2. 不要基于常识、猜测或历史对话补充答案。\n"
            "3. 不要编造来源、文件名或 chunk 编号。"
        )

    # 将每个文本块拼接成带 file_name、chunk_id、score 和引用格式的参考片段文本
    chunk_sections = "\n\n".join(
        [
            (
                f"[参考片段 {index + 1} | "
                f"file_name={item.get('file_name') or '当前文档'} | "
                f"chunk_id={item.get('chunk_id')} | "
                f"score={item.get('score', 0)} | "
                f"source={build_source_label(item)}]\n"
                f"{item['text']}"
            )
            for index, item in enumerate(matched_chunks)
        ]
    )

    # 在片段前加入统一引用规则，让模型知道必须把来源写到答案里
    return (
        "【检索结果】\n"
        "以下参考片段来自当前会话的知识库。\n\n"
        "回答要求：\n"
        "1. 请优先且只基于参考片段回答，不要编造文档中没有的信息。\n"
        "2. 涉及事实、结论、数字、规则、状态时，句末必须附引用。\n"
        "3. 引用格式必须使用：[来源: 文件名#chunk-n]。\n"
        "4. 至少使用 1 个引用；如果参考片段不足以回答，请明确说明“知识库中没有找到依据”。\n\n"
        f"{chunk_sections}"
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

    # 返回适合前端展示的引用摘要结构：
    # - file_name：命中文本块所属文件名
    # - chunk_id：文本块编号
    # - score：相关性分数
    # - source：和模型答案一致的引用来源标识
    # - text：命中的原文片段
    # - text_preview：正文预览，只截前 preview_limit 个字符
    # - text_length：原始文本总长度
    return [
        {
            "file_name": item.get("file_name"),
            "chunk_id": item.get("chunk_id"),
            "score": item.get("score", 0),
            "source": build_source_label(item),
            "text": item.get("text", ""),
            "text_preview": item.get("text", "")[:preview_limit],
            "text_length": len(item.get("text", ""))
        }
        for item in matched_chunks
    ]
