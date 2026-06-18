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
- 中文检索会优先使用连续双字 token，并通过命中数量和覆盖率过滤弱相关结果。
- 对“年假几天”“晚餐有吗”这类短中文问题，会放宽一次核心双字命中的门槛，避免短问句被过度过滤。
"""

#  导入正则表达式模块，用于re.findall(...)进行分词
import re
# 导入计数器，用于统计关键词出现次数
from collections import Counter
from typing import Any


# 关键词兜底检索的最少命中 token 数。低于该数量时，不认为 chunk 有可靠依据。
MIN_TOKEN_OVERLAP = 2
# 关键词兜底检索的最低问题覆盖率。命中 token 数 / query token 数低于该值时，视为弱相关。
MIN_QUERY_COVERAGE = 0.25
# 短中文问题的最大汉字长度。短问题 token 数少，不能完全套用长问题的最小命中数量。
MAX_SHORT_CHINESE_QUERY_LENGTH = 4
# 短中文问题的最低覆盖率。至少覆盖约三分之一 token 时，才允许单个核心双字命中通过。
MIN_SHORT_QUERY_COVERAGE = 1 / 3


def count_chinese_chars(text: str) -> int:
    """
    统计文本中的中文字符数量。

    函数说明：
    1. 只统计中文字符，不把标点、空格和英文数字计入短问句长度。
    2. 用于识别“年假几天”这类短中文问题。
    3. 不依赖具体业务关键词，避免检索器变成场景定制规则。

    :param text: 原始输入文本
    :return: 中文字符数量
    """
    # 使用正则找出所有中文字符，再统计数量
    return len(re.findall(r"[\u4e00-\u9fff]", text))


def is_short_chinese_query(text: str) -> bool:
    """
    判断当前 query 是否属于短中文问题。

    函数说明：
    1. 短中文问题通常只有一个核心实体和一个疑问成分。
    2. 这类问题的有效 token 较少，容易因为最小命中数量过高而 no_hit。
    3. 只基于长度判断，不引入业务关键词。

    :param text: 当前检索 query
    :return: 如果属于短中文问题则返回 True，否则返回 False
    """
    # 统计中文字符数量
    chinese_char_count = count_chinese_chars(text)
    # 中文字符数量在合理短问句范围内时，认为是短中文问题
    return 0 < chinese_char_count <= MAX_SHORT_CHINESE_QUERY_LENGTH


def tokenize(text: str) -> list[str]:
    """
    将输入文本切成轻量 token。

    函数说明：
    1. 英文、数字和下划线按连续单词切分。
    2. 中文按连续片段提取双字 token，减少单字误命中。
    3. 统一转小写，降低英文大小写带来的匹配差异。

    :param text: 待分词的原始文本，可以是用户问题或 chunk 正文
    :return: token 列表
    """
    # 统一大小写，保证 RAG、rag、Rag 这类英文词可以被同等匹配
    text = text.lower()
    # 英文、数字和下划线按连续单词切分
    word_tokens = re.findall(r"[a-z0-9_]+", text)
    # 中文按连续片段提取，后续再拆成双字 token
    chinese_segments = re.findall(r"[\u4e00-\u9fff]+", text)

    # 存放最终 token
    tokens = []
    # 保留英文和数字 token
    tokens.extend(word_tokens)

    # 中文使用双字 token，避免单字“公/司/是/否”等造成误命中
    for segment in chinese_segments:
        if len(segment) == 1:
            tokens.append(segment)
            continue
        tokens.extend(
            segment[index:index + 2]
            for index in range(len(segment) - 1)
        )

    # 过滤空白 token；不维护固定停用词表，相关性由命中数量和覆盖率统一判断
    return [
        token
        for token in tokens
        if token
    ]


def is_reliable_keyword_match(overlap_score: int, query_token_count: int, query: str) -> bool:
    """
    判断关键词命中是否足够可靠。

    函数说明：
    1. 使用最小命中数量过滤只有一个泛词命中的弱相关结果。
    2. 使用 query 覆盖率过滤长问题中零散命中的弱相关结果。
    3. 对短中文问题允许较少命中，避免“年假几天”这类短问句被误判为无依据。
    4. 不依赖业务关键词，避免后续不断补特定场景规则。

    :param overlap_score: query 与 chunk 的重叠 token 数
    :param query_token_count: query 的 token 总数
    :param query: 当前用户问题或检索 query
    :return: 是否属于可靠关键词命中
    """
    # 没有 query token 时，不能判断为可靠命中
    if query_token_count <= 0:
        return False

    # 计算 query 覆盖率，用于判断 chunk 是否覆盖了用户问题的足够部分
    coverage = overlap_score / query_token_count

    # 短中文问题通常只有一个核心双字词，允许一个命中通过，但仍要求覆盖率达标
    if (
        is_short_chinese_query(query)
        and overlap_score >= 1
        and coverage >= MIN_SHORT_QUERY_COVERAGE
    ):
        return True

    # 命中 token 数太少时，容易只是偶然重合
    if overlap_score < MIN_TOKEN_OVERLAP:
        return False

    # 覆盖率达标时，才认为这个 chunk 可以作为关键词兜底命中
    return coverage >= MIN_QUERY_COVERAGE


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

        # 只有命中数量和覆盖率都达标时，才把 chunk 作为可靠关键词兜底结果
        if is_reliable_keyword_match(
            overlap_score=overlap_score,
            query_token_count=sum(query_tokens.values()),
            query=query,
        ):
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
