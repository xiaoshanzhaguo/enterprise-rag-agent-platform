"""
前端请求封装模块。

职责：
1. 统一封装前端对后端接口的调用，包括文档索引、文档清理、流式请求发送、RAG 预览查询和 RAG 状态查询
2. 屏蔽具体接口地址与 requests 调用细节，避免这些网络请求逻辑散落在页面主代码中
3. 提供 SSE 事件流解析能力，将后端返回的流式响应整理为前端可直接消费的事件字典

说明：
- 当前模块属于前端与后端之间的请求适配层
- 不负责页面渲染，也不负责业务处理逻辑
- 主要作用是让 app.py 更简洁，并统一管理后端接口调用方式
- 适合当前项目“Streamlit 前端 + FastAPI 后端 + SSE 流式响应 + 第一阶段 RAG” 的工程结构
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
        timeout=60 # 请求最多等待 60 秒
    )

    # # 如果后端返回非 200 状态码，则视为索引失败。response.text 为后端返回的原始文本内容，例如错误信息。
    if response.status_code != 200:
        return False, f"文档索引失败: {response.text}"

    # 把后端返回的 JSON 响应解析成 Python 字典
    result = response.json()
    # 如果成功，就返回成功标记和一条提示文案。
    return True, f"文档索引完成，共切分 {result['chunk_count']} 个文本块。"


def clear_indexed_document(session_id: str) -> None:
    """
    调用后端清理接口，删除某个 session 对应的临时 RAG 文档索引。

    说明：
    - 该函数不阻断主流程
    - 即使清理失败，也不影响前端继续新建会话
    """
    try:
        # 删除这个会话的 RAG 索引
        requests.delete(
            f"{BACKEND_BASE_URL}/clear_document/{session_id}",
            timeout=10 # 最多等 10 秒。因为清理动作是辅助性的，不值得等太久。
        )
    except Exception:
        # 第一阶段先做静默失败，避免清理动作影响主流程
        pass


def post_stream_request(payload: dict, is_workflow: bool):
    """
    根据任务类型发送流式请求。
    :param payload: 请求体
    :param is_workflow: 是否是工作流模式
    :return: requests.Response 响应对象。该对象封装了后端返回的流式 HTTP 响应，调用方可继续通过 iter_lines() 逐行解析 SSE 事件。
    """
    # 根据当前模式选择后端接口：
    # - workflow 模式走 /workflow_stream
    # - 其他模式走 /chat_stream
    endpoint = "/workflow_stream" if is_workflow else "/chat_stream"
    # 发送流式请求到后端，并将 requests 返回的响应对象直接返回给调用方
    return requests.post(
        f"{BACKEND_BASE_URL}{endpoint}",
        json=payload, # 把请求体 JSON 发给后端
        stream=True, # 开启流式响应，适配 SSE 事件流
        timeout=120 # 最多等待120秒。因为模型流式生成会比普通接口更久
    )


def get_rag_preview(session_id: str, query: str, top_k: int) -> list[dict]:
    """
    获取当前 query 的 RAG 命中片段摘要。
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

    # 解析 JSON 响应，并取出里面的 chunks 字段。如果没有 chunks，就返回空列表
    return response.json().get("chunks", [])


def get_rag_status(session_id: str) -> dict:
    """
    获取当前 session 的 RAG store 状态。
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
    """
    # 逐行解析 SSE 事件流
    # # 按较小的数据块逐步读取响应流，减少 requests 缓冲等待时间，让 SSE 小事件更快到达前端
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
