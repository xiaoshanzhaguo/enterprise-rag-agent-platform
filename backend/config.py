"""
统一读取项目运行配置：
- 模型名称
- API Key
- Base URL
- RAG 相关参数
- Embedding 与向量库相关参数
最终封装为全局 settings 对象，供其他模块直接使用。
"""

import os
# 导入 dataclass 装饰器。使得能很方便的定义“配置对象”这种纯数据类，而不用自己手写很多初始化代码。
from dataclasses import dataclass

# 导入load_dotenv() 函数。将本地.env文件里的配置，加载到环境变量里。
from dotenv import load_dotenv


# 执行 .env 文件加载。让项目启动时，自动把 .env 文件里的内容读进来。
load_dotenv()


def _get_int_env(name: str, default: int) -> int:
    """
    读取”整数类型“的环境变量

    :param name: 环境变量名
    :param default: 默认值
    :return: 一个整数
    """
    try:
        # 从环境变量里取值，并转成整数；如果环境变量不存在，就用默认值。
        return int(os.getenv(name, str(default)))
    except ValueError:
        # 如果整数转换失败，就不要报错，直接退回默认值。
        return default


def _get_float_env(name: str, default: float) -> float:
    """
    读取”小数类型“的环境变量。

    :param name: 环境变量名
    :param default: 默认值
    :return: 一个小数
    """
    try:
        # 从环境变量里取值，并转成小数；如果环境变量不存在，就用默认值。
        return float(os.getenv(name, str(default)))
    except ValueError:
        # 如果小数转换失败，就不要报错，直接退回默认值。
        return default


def _get_bool_env(name: str, default: bool) -> bool:
    """
    读取”布尔类型“的环境变量。

    :param name: 环境变量名
    :param default: 默认值
    :return: 一个布尔值
    """
    # 从环境变量里读取原始字符串
    value = os.getenv(name)
    # 如果环境变量不存在或只写了空值，就使用默认值
    if value is None or not value.strip():
        return default
    # 统一转成小写，兼容 true / false 等常见写法
    normalized_value = value.strip().lower()
    # 这些值都视为开启
    if normalized_value in {"1", "true", "yes", "on"}:
        return True
    # 这些值都视为关闭
    if normalized_value in {"0", "false", "no", "off"}:
        return False
    # 遇到无法识别的布尔配置时，回退默认值
    return default


def _get_str_env(name: str, default: str) -> str:
    """
    读取”字符串类型“的环境变量。

    :param name: 环境变量名
    :param default: 默认值
    :return: 一个字符串
    """
    # 从环境变量里读取原始字符串
    value = os.getenv(name)
    # 如果环境变量不存在或只写了空值，就使用默认值
    if value is None or not value.strip():
        return default
    # 返回去掉前后空格后的配置值
    return value.strip()


def _get_optional_str_env(name: str) -> str | None:
    """
    读取”可选字符串类型“的环境变量。

    :param name: 环境变量名
    :return: 有效字符串或 None
    """
    # 从环境变量里读取原始字符串
    value = os.getenv(name)
    # 如果环境变量不存在或只写了空值，就返回 None
    if value is None or not value.strip():
        return None
    # 返回去掉前后空格后的配置值
    return value.strip()


# 把下面这个 Settings 类变成 dataclass。frozen=True 表示这个配置对象创建后，不允许再随意修改。
@dataclass(frozen=True)
# 定义一个配置类，把项目所有配置项都集中管理。可以理解为：一个“配置清单对象”。
class Settings:
    # 配置项：大模型名称
    llm_model: str = os.getenv("LLM_MODEL", "deepseek-chat")
    # 配置项：API Key
    api_key: str | None = os.getenv("DEEPSEEK_API_KEY")
    # 配置项：模型服务地址
    base_url: str | None = os.getenv("BASE_URL")
    # RAG 存储的 TTL（生存时间），单位是秒。表示一份 RAG 缓存最多保留多久。默认是1小时。
    rag_store_ttl_seconds: int = _get_int_env("RAG_STORE_TTL_SECONDS", 3600)
    # RAG 存储最多保留多少个 session。防止内存无限增长。
    rag_store_max_sessions: int = _get_int_env("RAG_STORE_MAX_SESSIONS", 50)
    # RAG 相关文本预览的最大字符数。前端证据卡片直接展示该预览，不再二次截断。
    rag_preview_text_limit: int = _get_int_env("RAG_PREVIEW_TEXT_LIMIT", 120)
    # 向量检索最低相似度阈值。低于该分数的结果会被视为没有可靠依据。
    rag_vector_score_threshold: float = _get_float_env("RAG_VECTOR_SCORE_THRESHOLD", 0.6)
    # 向量检索没有可靠命中时，是否允许回退到关键词检索。
    rag_keyword_fallback_enabled: bool = _get_bool_env("RAG_KEYWORD_FALLBACK_ENABLED", True)
    # 默认 SQLite 数据库地址。相对路径会基于项目根目录解析。
    database_url: str = os.getenv("DATABASE_URL", "sqlite:///./data/app.db")
    # Embedding 提供方。local 表示本地开源模型，openai 表示 OpenAI 兼容云端接口。
    embedding_provider: str = _get_str_env("EMBEDDING_PROVIDER", "local").lower()
    # Embedding 模型名称。用于把文档 chunk 和用户 query 转成向量。
    embedding_model: str = _get_str_env("EMBEDDING_MODEL", "BAAI/bge-m3")
    # 云端 Embedding 服务地址。为空时复用 BASE_URL。
    embedding_base_url: str | None = _get_optional_str_env("EMBEDDING_BASE_URL")
    # 云端 Embedding 服务 Key。为空时复用 DEEPSEEK_API_KEY。
    embedding_api_key: str | None = _get_optional_str_env("EMBEDDING_API_KEY")
    # ChromaDB 持久化目录。相对路径会基于项目根目录解析。
    vector_store_dir: str = _get_str_env("VECTOR_STORE_DIR", "./data/chroma")
    # RAG 检索模式。keyword 表示关键词检索，vector 表示 ChromaDB 向量检索。
    rag_retrieval_mode: str = _get_str_env("RAG_RETRIEVAL_MODE", "keyword").lower()


# 真正创建一个 Settings 实例对象。后面整个项目就可以统一用：settings.llm_model 来读取配置，而不用到处写os.getenv(...)。
settings = Settings()
