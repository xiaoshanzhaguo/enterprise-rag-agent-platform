"""
前端会话状态管理模块。

职责：
1. 初始化各功能模式对应的前端会话状态
2. 兼容读取旧版本本地 JSON 聊天历史，作为数据库历史恢复失败时的兜底方案
3. 清洗历史消息结构，避免异常数据影响页面渲染
4. 确保所有当前支持的模式都有独立 session_id 和消息列表
5. 构造发送给后端 API 的 history 上下文，控制历史长度并保留必要原始内容

说明：
- 当前项目主历史存储已迁移到后端 SQLite
- 本模块中的本地 JSON 读取逻辑主要用于旧版本兼容
- 本模块不负责后端数据库读写
- 本模块不负责页面渲染
- 适合当前项目“多模式会话隔离 + SQLite 历史持久化 + 旧 JSON 兜底”的前端状态管理场景
"""

# 读取旧版 chat_history_backup.json 时，把 JSON 字符串解析成 Python 字典
import json
# 拼接和定位本地历史文件路径
from pathlib import Path
# 生成新的唯一会话 ID
from uuid import uuid4


# 旧版本地历史文件路径。当前主流程已迁移到数据库，这里只作为兼容读取兜底
HISTORY_FILE = Path(__file__).resolve().parents[1] / "data" / "chat_history_backup.json"

# 发送给后端的最大历史消息条数，避免上下文过长
MAX_HISTORY_LENGTH = 6


def create_mode_sessions(mode_names: list[str]) -> dict:
    """
    为所有前端模式初始化独立会话。

    函数说明：
    - 每个模式都会拥有独立的 session_id
    - 每个模式都会拥有独立的 messages 消息列表
    - 用于页面首次启动、旧历史读取失败或补齐缺失模式时创建默认状态

    :param mode_names: 当前前端支持的模式名称列表
    :return: 模式会话字典
    """
    return {
        mode_name: {
            "session_id": str(uuid4()),
            "messages": []
        }
        for mode_name in mode_names
    }


def normalize_messages(messages: list) -> list[dict]:
    """
    清洗本地历史消息结构。

    函数说明：
    - 过滤掉非字典类型的异常消息
    - 过滤掉 role 或 content 不合法的消息
    - 保留合法的 role、content、raw_content
    - 清洗 workflow_blocks，确保分步结果结构可被前端安全渲染

    :param messages: 从旧版本地 JSON 文件读取出来的原始消息列表
    :return: 清洗后的消息列表
    """
    # 创建一个空列表，用来存放清洗后的消息
    normalized_messages = []

    # 遍历原始消息列表
    for message in messages:
        # 如果这条消息不是字典，说明结构不符合前端消息格式，直接跳过
        if not isinstance(message, dict):
            continue

        # 取出消息角色和展示内容
        role = message.get("role")
        content = message.get("content")

        # 只保留合法角色，并要求 content 必须是字符串
        if role not in {"user", "assistant", "system"} or not isinstance(content, str):
            continue

        # 构造一条最基础的干净消息
        normalized_message = {
            "role": role,
            "content": content
        }

        # 如果存在 raw_content，并且它是字符串，则保留原始内容
        raw_content = message.get("raw_content")
        if isinstance(raw_content, str):
            normalized_message["raw_content"] = raw_content

        # 如果存在 workflow_blocks，并且它是字典，则进一步清洗分步结果
        workflow_blocks = message.get("workflow_blocks")
        if isinstance(workflow_blocks, dict):
            normalized_message["workflow_blocks"] = {
                key: value
                for key, value in workflow_blocks.items()
                if isinstance(key, str) and isinstance(value, str)
            }

        normalized_messages.append(normalized_message)

    # 将清洗后的消息加入结果列表
    return normalized_messages


def load_mode_sessions(mode_names: list[str]) -> dict:
    """
    从旧版本地 JSON 文件恢复各模式会话历史。

    函数说明：
    - 优先尝试读取 data/chat_history_backup.json
    - 如果文件不存在、读取失败、JSON 损坏或结构异常，则返回默认空会话
    - 当前项目主流程已迁移到后端 SQLite，因此该函数主要作为历史兼容兜底

    :param mode_names: 当前前端支持的模式名称列表
    :return: 各模式对应的会话状态字典
    """
    # 先生成一份默认会话，作为任何异常情况下的兜底返回值
    default_sessions = create_mode_sessions(mode_names)

    # 如果旧版历史文件不存在，直接返回默认空会话
    if not HISTORY_FILE.exists():
        return default_sessions

    try:
        # 读取旧版历史文件，并将 JSON 文本解析为 Python 数据结构
        payload = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        # 文件读取失败或 JSON 格式损坏时，返回默认空会话
        return default_sessions

    # 从旧历史数据中取出 mode_sessions
    saved_sessions = payload.get("mode_sessions", {})
    if not isinstance(saved_sessions, dict):
        return default_sessions

    # 遍历当前支持的每个模式，并尝试恢复对应会话
    for mode_name in mode_names:
        # 取出这个模式对应的历史会话
        saved_session = saved_sessions.get(mode_name)

        # 如果这个模式的历史不是字典结构，则跳过
        if not isinstance(saved_session, dict):
            continue

        # 取出历史 session_id 和 messages
        session_id = saved_session.get("session_id")
        messages = saved_session.get("messages", [])

        # 用恢复出的会话替换默认会话
        default_sessions[mode_name] = {
            "session_id": str(session_id) if session_id else str(uuid4()),
            "messages": normalize_messages(messages if isinstance(messages, list) else [])
        }

    return default_sessions


def ensure_mode_sessions(mode_sessions: dict, mode_names: list[str]) -> dict:
    """
    确保所有当前支持的模式都有会话容器。

    函数说明：
    - 检查 mode_sessions 中是否包含所有当前模式
    - 如果某个模式缺失，则自动补一个新的空会话
    - 用于兼容新增模式或历史数据不完整的情况

    :param mode_sessions: 当前已有的模式会话字典
    :param mode_names: 当前前端支持的模式名称列表
    :return: 补齐后的模式会话字典
    """
    # 遍历所有模式
    for mode_name in mode_names:
        # 如果当前模式不存在，则补一个新的空会话
        if mode_name not in mode_sessions:
            mode_sessions[mode_name] = {
                "session_id": str(uuid4()),
                "messages": []
            }

    # 返回补齐后的会话状态
    return mode_sessions


def build_history_for_api(messages: list[dict], max_length: int = MAX_HISTORY_LENGTH) -> list[dict]:
    """
    构造发送给后端 API 的 history 消息列表。

    函数说明：
    - 从前端消息列表中截取最近 max_length 条
    - 只保留 role 和 content 两个字段
    - 文件上传消息优先使用 raw_content，保证后端拿到的是完整上下文
    - 用于构造 ChatRequest.history，避免把过长历史全部传给模型

    :param messages: 当前前端会话中的消息列表
    :param max_length: 最多保留的历史消息数量
    :return: 后端可直接接收的 history 列表
    """
    # 创建空列表，用来保存最终 history
    history = []

    # 只取最近 max_length 条消息，避免上下文过长
    recent_messages = messages[-max_length:]

    for message in recent_messages:
        role = message.get("role")

        # 上传文件场景下，content 是前端展示文本，raw_content 才是后端处理所需的完整文本
        content = message.get("raw_content", message.get("content", ""))

        # 只保留合法角色
        if role not in {"user", "assistant", "system"}:
            continue

        # 转换为后端 ChatRequest.history 可接收的结构
        history.append({
            "role": role,
            "content": content
        })

    return history
