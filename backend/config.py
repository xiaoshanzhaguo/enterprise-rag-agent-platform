"""
统一读取项目运行配置：
- 模型名称
- API Key
- Base URL
- RAG 相关参数
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
    # RAG 相关文本预览的最大字符数。
    rag_preview_text_limit: int = _get_int_env("RAG_PREVIEW_TEXT_LIMIT", 220)
    # 默认 SQLite 数据库地址。相对路径会基于项目根目录解析。
    database_url: str = os.getenv("DATABASE_URL", "sqlite:///./data/app.db")


# 真正创建一个 Settings 实例对象。后面整个项目就可以统一用：settings.llm_model 来读取配置，而不用到处写os.getenv(...)。
settings = Settings()
