"""
前端 API 请求封装模块。

职责：
1. 统一封装 Streamlit 前端对 FastAPI 后端接口的调用
2. 提供聊天历史恢复、最近会话列表、指定会话恢复、空会话创建、会话删除等会话管理请求能力
3. 提供文档索引、文档清理、RAG 引用预览查询和 RAG 状态查询能力
4. 提供聊天 / 工作流 / 轻量 Agent 流式请求发送能力
5. 提供 SSE 事件流解析能力，将后端返回的 data: {...} 事件转换为前端可直接消费的事件字典

说明：
- 当前模块属于前端请求适配层
- 不负责页面渲染
- 不负责模型调用
- 不负责业务逻辑处理
- 主要作用是屏蔽后端接口地址、requests 调用细节和 SSE 解析细节
- 让 app.py 专注于页面状态管理、用户输入处理和结果展示
- 适合当前项目“Streamlit 前端 + FastAPI 后端 + SSE 流式响应 + RAG + SQLite 历史持久化”的工程结构
"""

# JSON模块把后端返回的 JSON 字符串解析成 Python 字典，在 iter_sse_events() 里会用到
import json
# 读取环境变量
import os

# 发送 HTTP 请求给后端
import requests
# 读取本地 .env 文件里的环境变量
from dotenv import load_dotenv


# 加载本地 .env 配置，便于读取后端服务地址
load_dotenv()

# 后端基础地址：
# - 优先读取环境变量 FRONTEND_BACKEND_BASE_URL
# - 如果未配置，则默认使用本地开发地址
BACKEND_BASE_URL = os.getenv("FRONTEND_BACKEND_BASE_URL", "http://127.0.0.1:8000")


def load_chat_history(mode_names: list[str]) -> dict | None:
    """
    从后端数据库恢复各模式最近一次会话历史。

    函数说明：
    - 调用后端 /chat_history 接口
    - 根据前端模式列表，获取每个模式最近一次会话及其消息
    - 用于页面刷新后恢复聊天历史

    :param mode_names: 需要恢复历史的前端模式名称列表
    :return: 成功时返回 mode_sessions 字典；请求失败、状态码异常或响应结构异常时返回 None
    """
    try:
        # 调用后端历史恢复接口，把当前前端支持的模式列表作为请求体发送给后端
        response = requests.post(
            f"{BACKEND_BASE_URL}/chat_history",
            json={"mode_names": mode_names},
            timeout=10
        )
    except requests.RequestException:
        return None

    if response.status_code != 200:
        return None

    # 从后端响应中取出 mode_sessions，并校验它必须是字典结构
    mode_sessions = response.json().get("mode_sessions")
    if not isinstance(mode_sessions, dict):
        return None

    return mode_sessions


def list_recent_chat_sessions(limit: int = 10) -> list[dict]:
    """
    获取最近更新的聊天会话列表。

    函数说明：
    - 调用后端 GET /chat_sessions 接口
    - 默认获取最近10条非空会话
    - 用于左侧边栏展示历史会话入口
    - 请求失败时返回空列表，避免影响主页面使用

    :param limit: 最多获取多少条历史会话
    :return: 最近会话摘要列表；请求失败时返回空列表
    """
    try:
        # 调用后端最近会话列表接口
        response = requests.get(
            f"{BACKEND_BASE_URL}/chat_sessions",
            params={"limit": limit},
            timeout=10
        )
    except requests.RequestException:
        # 后端不可用时不阻断页面，只是不展示历史列表
        return []

    # 状态码异常时返回空列表
    if response.status_code != 200:
        return []

    # 读取响应中的 sessions 字段
    sessions = response.json().get("sessions", [])
    # 只有列表结构才返回给页面层
    if not isinstance(sessions, list):
        return []

    # 过滤掉异常元素，避免脏数据影响侧边栏渲染
    return [
        session
        for session in sessions
        if isinstance(session, dict)
    ]


def load_chat_session(session_id: str) -> dict | None:
    """
    获取指定聊天会话详情。

    函数说明：
    - 调用后端 GET /chat_session/{session_id} 接口
    - 获取会话所属模式和完整消息列表
    - 用于用户点击侧边栏历史会话后恢复对应聊天
    - 请求失败时返回 None，避免页面中断

    :param session_id: 需要恢复的会话ID
    :return: 会话详情字典；请求失败或结构异常时返回 None
    """
    try:
        # 调用后端指定会话详情接口
        response = requests.get(
            f"{BACKEND_BASE_URL}/chat_session/{session_id}",
            timeout=10
        )
    except requests.RequestException:
        # 网络异常时返回 None，由页面层决定是否提示
        return None

    # 状态码异常时返回 None
    if response.status_code != 200:
        return None

    # 解析响应 JSON
    session = response.json()
    # 必须是字典结构，且包含 session_id、mode、messages 三个关键字段
    if not isinstance(session, dict):
        return None

    # 消息列表必须是列表结构
    if not isinstance(session.get("messages"), list):
        return None

    # 返回会话详情
    return session


def create_chat_session(session_id: str, mode: str) -> None:
    """
    在后端数据库中创建一个空聊天会话。

    函数说明：
    - 调用后端 /chat_session 接口
    - 用于前端新建聊天或清空聊天后，同步创建新的数据库会话记录
    - 即使创建失败，也不阻断前端主流程

    :param session_id: 新会话ID
    :param mode: 当前模式所属模式
    :return: None
    """
    try:
        # 通知后端创建当前模式的新会话记录
        requests.post(
            f"{BACKEND_BASE_URL}/chat_session",
            json={
                "session_id": session_id,
                "mode": mode
            },
            timeout=10
        )
    except requests.RequestException:
        pass


def clear_chat_session(session_id: str) -> None:
    """
    从后端数据库中删除一个聊天会话及其关联数据。

    函数说明：
    - 调用后端 DELETE /chat_session/{session_id} 接口
    - 删除数据库中的会话、消息、文档、RAG 查询等关联数据
    - 后端会同时清理当前 session 对应的数据库 RAG 文档
    - 即使清理失败，也不阻断前端继续创建新会话

    :param session_id: 需要删除的会话ID
    :return: None
    """
    try:
        # 通知后端删除当前会话及其数据库关联数据
        requests.delete(
            f"{BACKEND_BASE_URL}/chat_session/{session_id}",
            timeout=10
        )
    except requests.RequestException:
        pass


def index_uploaded_document(session_id: str, file_name: str, document_text: str) -> tuple[bool, str]:
    """
    调用后端 /index_document 接口，为当前会话建立临时文档索引。

    :param session_id: 当前会话 ID
    :param file_name: 上传文件名
    :param document_text: 提取出来的完整文档文本
    :return: (True, 成功提示) 或 (False, 错误提示)
    """
    response = requests.post(
        f"{BACKEND_BASE_URL}/index_document",
        # 把请求体作为 JSON 发送给后端
        json={
            "session_id": session_id,
            "file_name": file_name,
            "document_text": document_text
        },
        timeout=300 # 请求最多等待 300 秒。本地 embedding 首次加载或下载模型时可能较慢。
    )

    # 如果后端返回非 200 状态码，则视为索引失败。response.text 为后端返回的原始文本内容，例如错误信息。
    if response.status_code != 200:
        return False, f"文档索引失败: {response.text}"

    # 把后端返回的 JSON 响应解析成 Python 字典
    result = response.json()
    # 如果成功，就返回成功标记和一条提示文案。
    return True, f"文档索引已完成，已生成 {result['chunk_count']} 个可检索文本块。"


def clear_indexed_document(session_id: str) -> None:
    """
    调用后端清理接口，删除某个 session 对应的数据库 RAG 文档索引。

    说明：
    - 该函数不阻断主流程
    - 即使清理失败，也不影响前端继续新建会话

    :param session_id: 会话ID
    :return: None
    """
    try:
        # 删除这个会话的 RAG 索引
        requests.delete(
            f"{BACKEND_BASE_URL}/clear_document/{session_id}",
            timeout=10 # 最多等 10 秒。因为清理动作是辅助性的，不值得等太久。
        )
    except requests.RequestException:
        # 第一阶段先做静默失败，避免清理动作影响主流程
        pass


def post_stream_request(payload: dict, task_type: str):
    """
    根据任务类型发送流式请求。

    :param payload: 请求体
    :param task_type: 后端任务类型，例如 chat、workflow 或 agent
    :return: requests.Response 响应对象。该对象封装了后端返回的流式 HTTP 响应，调用方可继续通过 iter_lines() 逐行解析 SSE 事件。
    """
    # 根据当前模式选择后端接口：
    # - workflow 模式走 /workflow_stream
    # - agent 模式走 /agent_stream
    # - 其他模式走 /chat_stream
    if task_type == "workflow":
        endpoint = "/workflow_stream"
    elif task_type == "agent":
        endpoint = "/agent_stream"
    else:
        endpoint = "/chat_stream"
    # 发送流式请求到后端，并将 requests 返回的响应对象直接返回给调用方
    return requests.post(
        f"{BACKEND_BASE_URL}{endpoint}",
        json=payload, # 把请求体 JSON 发给后端
        stream=True, # 开启流式响应，适配 SSE 事件流
        timeout=120 # 最多等待120秒。因为模型流式生成会比普通接口更久
    )


def get_rag_preview(session_id: str, query: str, top_k: int) -> list[dict]:
    """
    获取当前 query 的 RAG 命中引用和原文片段。

    函数说明：
    - 调用后端 /rag_preview 接口
    - 获取当前问题命中的文件名、chunk_id、score、引用来源和原文片段
    - 用于前端展示“本次回答引用了哪些来源”

    :param session_id: 当前会话ID
    :param query: 当前用户问题或检索 query
    :param top_k: 需要返回的命中片段数量
    :return: RAG 命中引用片段列表；请求失败时返回空列表
    """
    try:
        response = requests.post(
            f"{BACKEND_BASE_URL}/rag_preview",
            json={
                "session_id": session_id, # 当前会话ID
                "query": query, # 当前问题
                "top_k": top_k # 检索数量
            },
            timeout=20
        )
    # 如果请求过程中报网络异常，直接返回空列表
    except requests.RequestException:
        return []

    # 如果后端状态码不是 200，也直接返回空列表
    if response.status_code != 200:
        return []

    # 解析 JSON 响应，里面包含本次预览的检索方式和命中 chunks
    payload = response.json()
    # 读取本次实际检索方式，例如 vector 或 keyword
    retrieval_mode = payload.get("retrieval_mode", "unknown")
    # 取出 chunks 字段。如果没有 chunks，就返回空列表
    chunks = payload.get("chunks", [])

    # 将顶层 retrieval_mode 兜底写入每个 chunk，保证前端解释面板始终能展示检索方式
    for chunk in chunks:
        if isinstance(chunk, dict) and not chunk.get("retrieval_mode"):
            chunk["retrieval_mode"] = retrieval_mode

    # 返回补充后的命中片段列表
    return chunks


def get_rag_status(session_id: str) -> dict:
    """
    获取当前 session 的数据库 RAG 文档状态。

    函数说明：
    - 调用后端 /rag_status/{session_id} 接口
    - 查询当前会话是否已有索引文档、文档名、chunk 数量和过期时间

    :param session_id: 当前会话ID
    :return: 成功时返回 RAG 状态字典；请求失败时返回空字典
    """
    try:
        # 调用后端状态查询接口
        response = requests.get(
            f"{BACKEND_BASE_URL}/rag_status/{session_id}",
            timeout=10
        )
    # 如果请求异常，返回空字典
    except requests.RequestException:
        return {}

    # 如果状态码不是 200，返回空字典
    if response.status_code != 200:
        return {}

    # 如果成功，直接把后端返回的 JSON 解析成字典返回
    return response.json()


def iter_sse_events(response):
    """
    逐行解析 SSE 响应，产出事件字典。

    函数说明：
    - 读取后端 text/event-stream 流式响应
    - 只处理 data: 开头的 SSE 数据行
    - 将 data 后面的 JSON 字符串解析为 Python 字典
    - 使用 yield 将事件逐条交给外层 for 循环消费

    :param response: requests.Response 流式响应对象
    :return: 生成器对象；每次 yield 一条已解析的 SSE 事件字典
    """
    # 逐行解析 SSE 事件流
    # 按较小的数据块逐步读取响应流，减少 requests 缓冲等待时间，让 SSE 小事件更快到达前端
    # decode_unicode=True 让读取出来的内容直接是字符串，而不是 bytes
    for raw_line in response.iter_lines(chunk_size=1, decode_unicode=True):
        # 如果这一行是空的，就不处理。SSE 里经常会有空行，用来分隔事件
        if not raw_line:
            # 跳过当前这一轮循环，直接进入下一轮
            continue

        raw_text = raw_line.strip()

        # SSE 标准数据格式通常为：data: {...}
        # 如果这一行不是数据行，则跳过。
        if not raw_text.startswith("data: "):
            continue

        # 去掉前缀 "data: "，只保留后面的 JSON 字符串
        json_text = raw_text[6:]

        # 尝试把 JSON 字符串解析成 Python 字典。如果解析失败，就跳过这条坏数据
        try:
            event = json.loads(json_text)
        except json.JSONDecodeError:
            continue

        # 产出一条已解析的事件字典，供外层 for 循环逐条消费
        yield event
