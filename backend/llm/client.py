"""
大模型客户端创建模块。

职责：
1. 从统一配置中读取模型服务所需的 API Key 和 Base URL
2. 创建并返回可复用的大模型客户端
3. 将模型服务初始化逻辑与业务层解耦，方便后续切换不同模型服务商
"""
# 从 openai Python SDK 里导入 OpenAI 类，OpenAI 类用于后续创建大模型客户端。
from openai import OpenAI

# 从自定义的配置模块 backend.config 里导入 settings 对象，settings 对象里已经统一保存了项目配置。
from backend.config import settings


def get_client() -> OpenAI:
    """
    创建并返回统一的大模型客户端。

    通过环境变量读取 API Key 和 Base URL,
    便于后续切换不同模型服务商时，避免修改业务层代码。
    """
    # 从统一配置中读取模型服务鉴权信息和接口地址
    api_key = settings.api_key
    base_url = settings.base_url

    # 如果未配置 API Key，则直接报错，避免后续调用失败
    if not api_key:
        raise ValueError("未检测到 DEEPSEEK_API_KEY，请检查 .env 配置。")

    # 如果未配置服务地址，则直接报错
    if not base_url:
        # 让错误尽量在“初始化阶段”暴露，而不是等业务层调用模型时才发现。
        raise ValueError("未检测到 BASE_URL，请检查 .env 配置。")

    # 创建并返回 OpenAI 兼容客户端
    return OpenAI(
        api_key=api_key,
        base_url=base_url
    )
