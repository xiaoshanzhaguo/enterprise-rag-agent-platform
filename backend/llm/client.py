"""
大模型客户端创建模块。

职责：
1. 从统一配置中读取模型服务所需的 API Key 和 Base URL
2. 创建并返回可复用的 LangChain ChatModel（LangChain 的聊天对象模型）
3. 将模型服务初始化逻辑与业务层解耦，方便后续切换不同模型服务商（也就是说，业务层不用关心 API Key、Base URL、DeepSeek 客户端怎么创建，只需要调用：get_client()，就能拿到一个可用的 LangChain ChatModel。）
4. 单独保留 OpenAI 兼容客户端，供云端 Embedding 调用
"""

# 缓存模型实例，避免每次请求都重复创建 HTTP 客户端。
# lru_cache 是 Python 标准库里的缓存装饰器。它的作用是：让某个函数的返回结果被缓存起来。
from functools import lru_cache

# BaseChatModel 是 LangChain 对聊天模型的统一抽象，业务层只依赖这个接口。
# BaseChatModel 是 LangeChain 里聊天模型的基础抽象类型。可以理解为：只要是 LangChain 的聊天模型，一般都可以看成 BaseChatModel。
# 比如：ChatDeepSeek、ChatOpenAI、ChatTongyi 都属于聊天模型。
# 这里导入 BaseChatModel 主要用于类型标注：def get_client() -> BaseChatModel: 意思是，get_client 返回的是一个 LangChain 聊天模型对象。
from langchain_core.language_models.chat_models import BaseChatModel
# ChatDeepSeek 是 LangChain 官方 DeepSeek 集成，支持 invoke 和 stream。
# 这一行导入 DeepSeek 的 LangChain 聊天模型类。后面会用它创建模型：return ChatDeepSeek(...)。可以理解为：ChatDeepSeek 是 LangChain 封装好的 DeepSeek 聊天模型。它通常支持 invoke: 一次性调用模型、stream: 流式调用模型，所以可以配合项目里的 SSE 流式输出。
from langchain_deepseek import ChatDeepSeek
# OpenAI SDK 继续用于云端 Embedding，不再负责聊天模型调用。
# 导入 OpenAI 官方 SDK 里的 OpenAI 客户端类。意思是：聊天模型调用已经交给 LangChain 的 ChatDeepSeek; 但 Embedding 调用还继续使用 OpenAI SDK。因为很多 OpenAI 兼容服务都支持类似：client.embeddings.create(...)，所以这里保留 OpenAI 兼容客户端。
from openai import OpenAI, max_retries

# 从自定义的配置模块 backend.config 里导入 settings 对象，settings 对象里已经统一保存了项目配置。
from backend.config import settings


# 定义一个函数，前面的_表示：这是模块内部使用的辅助函数，不建议外部直接使用。
def _validate_llm_settings() -> tuple[str, str]:
    """
    校验并返回模型服务鉴权信息。（说明这个函数用于校验模型配置。）

    :return: 二元组，依次为 API Key 和 Base URL
    """
    # 从统一配置中读取模型服务鉴权信息和接口地址
    api_key = settings.api_key
    base_url = settings.base_url  # 从 settings.api_key 中读取模型服务地址。

    # 如果未配置 API Key（如果 api_key 是 None 或者 空字符串''），则直接报错，避免后续调用失败
    if not api_key:
        # 如果没有 API Key，就主动抛出错误。raise ValueError(...) 的作用是：中断程序，并告诉开发者配置缺失。这样比等真正调用模型时才报错更好排查。
        raise ValueError("未检测到 DEEPSEEK_API_KEY，请检查 .env 配置。")

    # 如果未配置服务地址，则直接报错
    if not base_url:
        # 让错误尽量在“初始化阶段”暴露，而不是等业务层调用模型时才发现。
        raise ValueError("未检测到 BASE_URL，请检查 .env 配置。")

    # 配置完整时返回给具体客户端创建函数复用。
    # 返回的是一个元组：(api_key, base_url)，后面可以这样接收：api_key, base_url = _validate_llm_settings()
    return api_key, base_url

# @lru_cache(maxsize=1) 是装饰器，它修饰下面的函数：def get_client() -> BaseChatModel:。maxsize=1 表示最多缓存1个结果。因为项目通常只需要一个聊天模型客户端，所以缓存一个就够了。
# 作用是：第一次调用 get_client() 创建 ChatDeepSeek；后面再次调用 get_client() 时，直接复用同一个 ChatDeepSeek 实例。
# 这样可以避免每次请求都重新创建 HTTP 客户端，提高性能。
@lru_cache(maxsize=1)
def get_client() -> BaseChatModel:
    """
    创建并缓存 LangChain ChatModel。

    函数说明：
    1. FastAPI 路由仍调用原来的 get_client()，因此接口层不需要改名。
    2. 返回值从 OpenAI SDK 客户端替换为 LangChain BaseChatModel。
    3. 业务层通过 invoke_chat_model / stream_chat_model 调用模型。
    4. ChatDeepSeek 支持 invoke 和 stream，可以继续配合现有 SSE 生成器。

    :return: LangChain BaseChatModel 实例
    """
    # 统一校验环境变量，避免模型真正调用时才发现配置缺失。
    # 这里用到了元组解包。
    api_key, base_url = _validate_llm_settings()

    max_retries = settings.max_retries

    # 创建 DeepSeek ChatModel；api_base 继续复用项目原有 BASE_URL。
    # return 表示创建完成后直接把这个模型对象返回给调用方。
    return ChatDeepSeek(
        model=settings.llm_model,  # 指定使用哪个模型
        api_key=api_key,           # 把刚才校验过的 API Key 传给 ChatDeepSeek，用于模型服务鉴权。
        api_base=base_url,         # 把 Base URL 传给 ChatDeepSeek，意思是告诉客户端：请求要发到哪个模型服务地址。
        max_retries=max_retries,   # 设置最大重复次数为，意思是：如果模型请求因为网络波动、临时错误失败，客户端最多重试 x 次。
    )

# @lru_cache(maxsize=1) 给下面的函数加了缓存，表示：get_openai_compatible_client() 第一次调用时创建 OpenAI 客户端；后面重复调用时复用同一个客户端。
@lru_cache(maxsize=1)
def get_openai_compatible_client() -> OpenAI:
    """
    创建并缓存 OpenAI 兼容客户端。（这个 OpenAI 客户端主要用于：client_embedding_create()）
    该客户端只供云端 Embedding 使用，聊天模型统一走 LangChain ChatModel。
    这个函数和 get_client() 的区别是：
    - get_client() 返回 LangChain ChatModel，用于聊天模型调用。
    - get_openai_compatible_client() 返回 OpenAI SDK 客户端，用于 Embedding。

    :return: OpenAI 兼容客户端（即 OpenAI SDK 客户端实例）
    """
    # 云端 Embedding 没有独立配置时，复用大模型的 Key 和 Base URL。这样做简单，但以后如果聊天模型和 Embedding 模型来自不同服务商，可以单独拆配置。
    api_key, base_url = _validate_llm_settings()

    # 创建 OpenAI 兼容客户端，保留 embeddings.create 调用能力。
    # 开始创建 OpenAI SDK 客户端并返回。
    return OpenAI(
        api_key=api_key,  # 传入 API Key，用于鉴权。
        base_url=base_url # 传入 OpenAI SDK 兼容接口地址。比如某些平台会提供：https://xxx.com/v1，让你用 OpenAI SDK 的方式调用。
    )
