"""
Embedding 客户端模块。

职责：
1. 复用项目已有的大模型客户端创建入口。
2. 从统一配置中读取 embedding 模型名称。
3. 调用 OpenAI 兼容的 embeddings 接口，为 query 或文档 chunk 生成向量。
4. 为 RAG 向量检索层提供统一的 embedding 生成入口。

说明：
- 当前模块复用项目已有的 OpenAI 兼容服务配置。
- EMBEDDING_MODEL 控制使用哪个 embedding 模型。
- API Key 和 Base URL 的校验逻辑统一放在 backend.llm.client.get_client 中。
- 后续如果要把 embedding 服务和聊天模型服务拆开，可以再扩展独立的 embedding 客户端入口。
"""

# 导入统一配置对象，读取 embedding 模型名
from backend.config import settings
# 导入项目已有的大模型客户端创建函数，避免重复维护 OpenAI 客户端初始化逻辑
from backend.llm.client import get_client


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
    # 过滤空字符串，避免 embedding 接口收到空输入
    cleaned_texts = [text for text in texts if text and text.strip()]

    # 如果没有有效文本，直接返回空列表
    if not cleaned_texts:
        return []

    # 创建 embedding 客户端
    client = get_client()

    # 调用 OpenAI 兼容 embedding 接口
    response = client.embeddings.create(
        model=settings.embedding_model,
        input=cleaned_texts
    )

    # 按返回顺序取出 embedding 向量
    return [item.embedding for item in response.data]
