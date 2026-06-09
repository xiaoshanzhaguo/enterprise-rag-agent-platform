"""
RAG 检索器模块。

职责：
1. 将用户问题和文本块内容切成可比较的轻量 token。
2. 使用关键词重叠和整句命中奖励，为当前会话的文档 chunk 计算相关性分数。
3. 返回分数最高的若干文本块，供 RAG 服务层组装 prompt 上下文和前端引用预览。

说明：
- 当前实现是第一阶段轻量版检索，不依赖 embedding 或向量数据库。
- 分数只表示关键词匹配强度，用于快速落地、调试和解释检索结果。
- 当问题没有有效 token 或文本块没有命中时，返回空列表，让上层明确处理“没有依据”的场景。
"""

#  导入正则表达式模块，用于re.findall(...)进行分词
import re
# 导入计数器，用于统计关键词出现次数
from collections import Counter
from typing import Any


def tokenize(text: str) -> list[str]:
    """
    将输入文本切成轻量 token。

    函数说明：
    1. 英文、数字和下划线按连续单词切分。
    2. 中文按单字切分。
    3. 统一转小写，降低英文大小写带来的匹配差异。

    :param text: 待分词的原始文本，可以是用户问题或 chunk 正文
    :return: token 列表
    """
    # 统一大小写，保证 RAG、rag、Rag 这类英文词可以被同等匹配
    text = text.lower()
    # 第一阶段先使用正则分词，保持实现轻量、可解释，后续可替换为更专业的分词器
    return re.findall(r"[\u4e00-\u9fff]|[a-z0-9_]+", text)


def retrieve_top_chunks(query: str, chunks: list[dict[str, Any]], top_k: int = 3) -> list[dict[str, Any]]:
    """
    根据用户 query，从文本块里检索最相关的 top_k 个片段。

    函数说明：
    1. 对 query 和每个 chunk 分别分词。
    2. 用 token 重叠数量计算基础相关性分数。
    3. 如果 query 整句出现在 chunk 中，额外增加 phrase_bonus。
    4. 按分数降序、chunk_id 升序返回前 top_k 个命中块。

    :param query: 当前用户问题
    :param chunks: 候选文本块列表，每个 chunk 至少应包含 text 和 chunk_id
    :param top_k: 最多返回的文本块数量，默认 3
    :return: 命中的 chunk 列表；每个返回项会额外带上 score 字段
    """
    if not chunks:
        return []

    # query分词
    query_tokens = Counter(tokenize(query))

    # 没有有效 query token 时不做兜底返回，避免生成看似有来源但实际无关的引用
    if not query_tokens:
        return []

    # 存放打分后的 chunk
    scored_chunks = []

    for chunk in chunks:
        chunk_text = chunk["text"]
        # chunk 分词
        chunk_tokens = Counter(tokenize(chunk_text))

        # 对重复出现的关键词按最小出现次数计分，兼顾词频，又避免单个词无限拉高分数
        overlap_score = sum(
            min(query_tokens[token], chunk_tokens[token])
            for token in query_tokens
        )

        # 整句命中通常比零散关键词更可靠，因此给一个小的固定加分
        phrase_bonus = 2 if query.strip() and query.lower() in chunk_text.lower() else 0

        total_score = overlap_score + phrase_bonus

        # 过滤无关 chunk 并保存结果
        if total_score > 0:
            scored_chunks.append({
                **chunk,
                "score": total_score
            })

    # 没有任何命中时交给服务层输出“知识库中没有找到依据”
    if not scored_chunks:
        return []

    # 同分时按 chunk_id 稳定排序，避免同一输入在不同运行中出现引用顺序抖动
    scored_chunks.sort(key=lambda item: (-item["score"], item["chunk_id"]))

    return scored_chunks[:top_k]
