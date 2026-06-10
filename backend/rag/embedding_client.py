"""
Embedding 客户端模块。

职责：
1. 根据 EMBEDDING_PROVIDER 在本地 embedding 和云端 embedding 之间切换。
2. 本地模式默认使用 BAAI/bge-m3，便于离线演示和降低费用风险。
3. 云端模式兼容 OpenAI SDK，便于后续接入 OpenAI、火山方舟或其他兼容服务。
4. 为 RAG 向量检索层提供统一的 embedding 生成入口。

说明：
- local 模式会通过 sentence-transformers 加载本地开源模型。
- openai 模式会优先读取 EMBEDDING_BASE_URL / EMBEDDING_API_KEY。
- 如果没有单独配置云端 embedding 地址和 Key，则回退复用项目已有的大模型客户端。
- EMBEDDING_MODEL 控制使用哪个 embedding 模型。
"""

from __future__ import annotations

# 导入缓存装饰器，避免每次生成 embedding 都重复加载本地模型
from functools import lru_cache
from typing import Any

# 导入 OpenAI 兼容客户端，用于云端 embedding 独立配置场景
from openai import OpenAI

# 导入统一配置对象，读取 embedding 提供方、模型名和云端配置
from backend.config import settings
# 导入项目已有的大模型客户端创建函数，避免重复维护 OpenAI 客户端初始化逻辑
from backend.llm.client import get_client


@lru_cache(maxsize=1)
def get_local_embedding_model() -> Any:
    """
    加载并缓存本地 embedding 模型。

    函数说明：
    1. 延迟导入 sentence-transformers，避免 keyword 模式启动时强制加载大依赖。
    2. 使用 settings.embedding_model 指定本地模型名称。
    3. 通过 lru_cache 缓存模型对象，避免每次请求都重新加载模型。

    :return: sentence-transformers 模型对象
    """
    try:
        # 延迟导入本地 embedding 依赖，只有 local provider 真正使用时才加载
        # sentence_transformers: 专门用来生成文本向量的工具包, SentenceTransformer: 向量模型加载器
        from sentence_transformers import SentenceTransformer
    except ImportError as exc:
        raise ImportError(
            "当前启用了本地 embedding，但未安装 sentence-transformers，请先执行 pip install sentence-transformers。"
        ) from exc

    # 加载本地 embedding 模型；首次运行会从模型仓库下载，之后会复用本地缓存
    return SentenceTransformer(settings.embedding_model)


def get_remote_embedding_client():
    """
    获取云端 embedding 客户端。

    函数说明：
    1. 优先读取 EMBEDDING_BASE_URL 和 EMBEDDING_API_KEY。
    2. 如果没有单独配置 embedding 云端服务，则复用项目已有 get_client()。
    3. 返回 OpenAI 兼容客户端，用于调用 embeddings.create。

    :return: OpenAI 兼容客户端
    """
    # 读取云端 embedding 独立服务地址；为空时表示复用大模型客户端
    base_url = settings.embedding_base_url
    # 读取云端 embedding 独立 Key；为空时表示复用大模型客户端
    api_key = settings.embedding_api_key

    # 如果没有单独配置云端 embedding 地址和 Key，则复用已有大模型客户端创建逻辑
    if not base_url and not api_key:
        return get_client()

    # 如果只配置了一半，就主动报错，避免请求时才出现更难定位的问题
    if not base_url or not api_key:
        raise ValueError("EMBEDDING_BASE_URL 和 EMBEDDING_API_KEY 需要同时配置。")

    # 创建独立的 OpenAI 兼容 embedding 客户端
    return OpenAI(
        api_key=api_key,
        base_url=base_url,
    )


def generate_embedding(text: str) -> list[float]:
    """
    为单段文本生成 embedding。

    函数说明：
    1. 接收一段文本。
    2. 调用 embedding 模型生成向量。
    3. 返回 float 列表，供向量库写入或查询使用。

    :param text: 需要生成 embedding 的文本
    :return: embedding 向量
    """
    # 调用批量函数，保持单条和多条生成逻辑一致
    embeddings = generate_embeddings([text])
    # 返回第一条 embedding；如果服务异常未返回，给出空列表
    return embeddings[0] if embeddings else []


def generate_embeddings(texts: list[str]) -> list[list[float]]:
    """
    为多段文本批量生成 embedding。

    函数说明：
    1. 过滤空文本，避免向 embedding 服务发送无意义内容。
    2. 使用 settings.embedding_model 指定模型。
    3. 按服务返回顺序提取 embedding 列表。

    :param texts: 需要生成 embedding 的文本列表
    :return: embedding 向量列表
    """
    # 过滤空字符串，避免 embedding 模型收到空输入
    cleaned_texts = [text for text in texts if text and text.strip()]

    # 如果没有有效文本，直接返回空列表
    if not cleaned_texts:
        return []

    # 本地模式使用开源 embedding 模型，适合演示、离线运行和控制成本
    if settings.embedding_provider == "local":
        return generate_local_embeddings(cleaned_texts)

    # 云端 OpenAI 兼容模式，适合后续接入 OpenAI、火山方舟、阿里云等服务
    if settings.embedding_provider == "openai":
        return generate_remote_embeddings(cleaned_texts)

    # 如果配置了未知 provider，则主动报错，避免静默走错模型
    raise ValueError(f"不支持的 EMBEDDING_PROVIDER：{settings.embedding_provider}")


def generate_local_embeddings(texts: list[str]) -> list[list[float]]:
    """
    使用本地开源模型生成 embedding。

    函数说明：
    1. 加载并复用本地 sentence-transformers 模型。
    2. 对输入文本批量生成向量。
    3. 开启 normalize_embeddings，便于后续向量相似度检索。

    :param texts: 已过滤后的有效文本列表
    :return: embedding 向量列表
    """
    # 获取缓存后的本地 embedding 模型
    model = get_local_embedding_model()
    # 批量生成归一化向量，减少后续相似度计算的尺度差异
    vectors = model.encode(
        texts,
        normalize_embeddings=True, # 自动归一化向量，只比较方向，不比较长度，使得向量检索更稳定
    )
    # 将 numpy 向量转换为普通 Python list，便于 ChromaDB 写入
    return [
        vector.tolist() if hasattr(vector, "tolist") else list(vector)
        for vector in vectors
    ]


def generate_remote_embeddings(texts: list[str]) -> list[list[float]]:
    """
    使用 OpenAI 兼容云端接口生成 embedding。

    函数说明：
    1. 获取云端 embedding 客户端。
    2. 使用 settings.embedding_model 指定云端模型。
    3. 按服务返回顺序提取 embedding 列表。

    :param texts: 已过滤后的有效文本列表
    :return: embedding 向量列表
    """
    # 创建或复用 OpenAI 兼容 embedding 客户端
    client = get_remote_embedding_client()
    # 调用 OpenAI 兼容 embedding 接口
    response = client.embeddings.create(
        model=settings.embedding_model,
        input=texts
    )

    # 按返回顺序取出 embedding 向量
    return [item.embedding for item in response.data]
