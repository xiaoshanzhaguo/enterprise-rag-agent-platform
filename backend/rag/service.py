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
- 当 RAG_KEYWORD_FALLBACK_ENABLED=true 时，向量无可靠命中才回退到关键词检索
- 适合本地开发、项目演示和求职场景下的工程化展示
"""
from typing import Any

from backend.config import settings
from backend.db.repository import save_rag_query_with_hits
from backend.rag.retriever import retrieve_top_chunks
from backend.rag.store import get_document_chunks
from backend.rag.vector_store import retrieve_similar_chunks


NO_RAG_EVIDENCE_MESSAGE = "知识库中没有找到依据"
NO_HIT_RETRIEVAL_MODE = "no_hit"


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


def attach_retrieval_metadata(chunks: list[dict[str, Any]], retrieval_mode: str) -> list[dict[str, Any]]:
    """
    为检索结果补充统一的解释性字段。

    函数说明：
    1. 给每个 chunk 补充 rank，保证前端和数据库都有稳定排序。
    2. 给每个 chunk 补充 retrieval_mode，记录本次实际使用的检索方式。
    3. 返回新的 chunk 列表，避免直接修改调用方传入的原始对象。

    :param chunks: 原始检索结果列表
    :param retrieval_mode: 实际检索方式，例如 vector 或 keyword
    :return: 补充解释字段后的检索结果列表
    """
    # 存放补充元数据后的检索结果
    enriched_chunks = []

    # 遍历检索结果，并从 1 开始生成稳定排名
    for index, chunk in enumerate(chunks, start=1):
        # 复制一份 chunk，避免修改原始数据结构
        enriched_chunk = dict(chunk)
        # 如果检索器没有返回 rank，则使用当前顺序兜底
        enriched_chunk["rank"] = enriched_chunk.get("rank") or index
        # 标记本次命中的实际检索方式，便于前端解释和数据库记录
        enriched_chunk["retrieval_mode"] = retrieval_mode
        # 加入最终结果列表
        enriched_chunks.append(enriched_chunk)

    # 返回补充后的结果
    return enriched_chunks


def resolve_retrieval_mode(matched_chunks: list[dict[str, Any]]) -> str:
    """
    根据命中结果判断本次实际使用的检索方式。

    函数说明：
    1. 如果命中结果里已经携带 retrieval_mode，则优先使用该值。
    2. 如果没有命中结果，则返回 no_hit，避免把无依据查询误记为 vector 或 keyword 命中。
    3. 该值用于 /rag_preview 展示和 rag_queries 日志记录。

    :param matched_chunks: 本次检索命中的 chunk 列表
    :return: 检索方式字符串
    """
    # 遍历命中结果，优先读取检索器标记的实际检索方式
    for chunk in matched_chunks:
        retrieval_mode = chunk.get("retrieval_mode")
        if retrieval_mode:
            return str(retrieval_mode)

    # 没有命中结果时，明确标记为 no_hit，表示最终没有可靠依据
    return NO_HIT_RETRIEVAL_MODE


def retrieve_keyword_chunks(session_id: str | None, query: str, top_k: int = 3) -> list[dict[str, Any]]:
    """
    使用关键词检索获取当前会话的 RAG 文本块。

    函数说明：
    1. 从数据库读取当前 session 已保存的 document_chunks。
    2. 使用轻量关键词检索器计算 query 与 chunk 的相关性。
    3. 返回和向量检索兼容的 chunk 字典列表。

    :param session_id: 当前会话 ID，可以是字符串，也可以是 None
    :param query: 当前用户问题
    :param top_k: 最多返回几个最相关的块，默认 3
    :return: 关键词检索命中的 chunk 列表
    """
    # 如果当前没有 session_id，则无法定位到对应会话的文档
    if not session_id:
        return []

    # 从数据库中取出当前会话已索引的文本块
    chunks = get_document_chunks(session_id)
    if not chunks:
        return []

    # 根据 query 从所有文本块中检索最相关的前 top_k 个
    keyword_chunks = retrieve_top_chunks(query=query, chunks=chunks, top_k=top_k)
    # 给关键词检索结果补充 rank 和 retrieval_mode，便于前端解释和数据库记录
    return attach_retrieval_metadata(keyword_chunks, "keyword")


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
    # 复用带检索方式的检索函数，只返回 chunk 列表，兼容旧调用方
    matched_chunks, _retrieval_mode = retrieve_rag_chunks_with_mode(
        session_id=session_id,
        query=query,
        top_k=top_k
    )
    return matched_chunks


def retrieve_rag_chunks_with_mode(session_id: str | None, query: str, top_k: int = 3) -> tuple[list[dict[str, Any]], str]:
    """
    根据 session_id 和 query 获取 RAG 文本块，并返回本次实际检索结果状态。

    函数说明：
    1. vector 命中时返回 chunk 列表和 vector。
    2. vector 未命中但 keyword fallback 命中时返回 chunk 列表和 keyword。
    3. 最终没有可靠命中时返回空列表和 no_hit。

    :param session_id: 当前会话 ID，可以是字符串，也可以是 None
    :param query: 当前用户问题
    :param top_k: 最多返回几个最相关的块，默认 3
    :return: 二元组，第一项是命中 chunk 列表，第二项是实际检索状态
    """
    # 如果当前没有 session_id，则无法定位到对应会话的文档
    if not session_id:
        return [], NO_HIT_RETRIEVAL_MODE

    # 如果配置为向量模式，则直接通过 ChromaDB 按语义相似度检索
    if settings.rag_retrieval_mode == "vector":
        vector_chunks = retrieve_similar_chunks(
            session_id=session_id,
            query=query,
            top_k=top_k
        )
        # 如果向量检索命中可靠结果，直接返回向量结果
        if vector_chunks:
            return attach_retrieval_metadata(vector_chunks, "vector"), "vector"

        # 如果关闭了关键词兜底，则向量无可靠命中时直接返回空列表
        if not settings.rag_keyword_fallback_enabled:
            return [], NO_HIT_RETRIEVAL_MODE

        # 如果向量检索没有可靠命中，并且允许 fallback，则回退到严格关键词检索。
        # 这样“远程办公需要怎样申请”这类明确词面命中不会被向量阈值误过滤。
        keyword_chunks = retrieve_keyword_chunks(
            session_id=session_id,
            query=query,
            top_k=top_k
        )
        if keyword_chunks:
            return keyword_chunks, "keyword"
        return [], NO_HIT_RETRIEVAL_MODE

    # keyword 模式下直接使用关键词检索
    keyword_chunks = retrieve_keyword_chunks(
        session_id=session_id,
        query=query,
        top_k=top_k
    )
    if keyword_chunks:
        return keyword_chunks, "keyword"
    return [], NO_HIT_RETRIEVAL_MODE


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
                f"retrieval_mode={item.get('retrieval_mode') or resolve_retrieval_mode(matched_chunks)} | "
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
    matched_chunks, retrieval_mode = retrieve_rag_chunks_with_mode(
        session_id=session_id,
        query=query,
        top_k=top_k
    )

    # 持久化记录本次 RAG 查询及其命中的文本块，便于后续追踪检索效果和调试
    save_rag_query_with_hits(
        session_id=session_id,
        query_text=query,
        top_k=top_k,
        matched_chunks=matched_chunks,
        retrieval_mode=retrieval_mode
    )

    # 再把检索结果转换成 prompt 可直接使用的上下文字符串
    return build_rag_context_from_chunks(matched_chunks)


def build_rag_preview(session_id: str | None, query: str, top_k: int = 3) -> list[dict[str, Any]]:
    """
    构造给前端展示的检索片段摘要。
    """
    # 先检索最相关的文本块
    matched_chunks, _retrieval_mode = retrieve_rag_chunks_with_mode(
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
            "rank": item.get("rank", index),
            "file_name": item.get("file_name"),
            "chunk_id": item.get("chunk_id"),
            "score": item.get("score", 0),
            "retrieval_mode": item.get("retrieval_mode") or resolve_retrieval_mode(matched_chunks),
            "source": build_source_label(item),
            "text": item.get("text", ""),
            "text_preview": item.get("text_preview") or item.get("text", "")[:preview_limit],
            "text_length": len(item.get("text", ""))
        }
        for index, item in enumerate(matched_chunks, start=1)
    ]
