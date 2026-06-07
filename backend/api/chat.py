"""
API 路由模块。

职责：
1. 注册前端可调用的后端接口
2. 提供聊天流式响应接口
3. 提供工作流流式响应接口
4. 提供 RAG 文档索引、检索预览、状态查询与清理接口
5. 提供聊天历史恢复接口
6. 提供聊天会话创建与删除接口
7. 统一作为前端与业务服务层之间的请求入口

说明：
- 当前模块属于 API（接口层）
- 不负责具体业务逻辑实现
- 不负责模型调用细节
- 不负责数据库操作实现
- 聊天与工作流逻辑由 Service 层负责
- 数据持久化由 Repository 层负责
- 文档切块、存储、检索与状态管理由 RAG 模块负责
- 前端所有业务请求均通过本模块进入系统

当前已提供接口：

聊天相关：
- POST /chat_stream
- POST /workflow_stream

聊天历史相关：
- POST /chat_history
- POST /chat_session
- DELETE /chat_session/{session_id}

RAG 相关：
- POST /index_document
- POST /rag_preview
- GET /rag_status/{session_id}
- DELETE /clear_document/{session_id}

适用场景：
- 前后端分离架构
- SSE 流式响应
- RAG 第一阶段（内存检索）
- SQLite 持久化聊天历史
"""

from fastapi import APIRouter, HTTPException

from backend.llm.client import get_client
from backend.rag.chunker import split_text_into_chunks
from backend.rag.store import save_document_chunks
from backend.rag.store import clear_document_chunks
from backend.rag.store import get_document_status
from backend.rag.service import build_rag_preview
from backend.db.repository import delete_chat_session
from backend.db.repository import ensure_chat_session
from backend.db.repository import load_latest_mode_sessions
from backend.schema.chat_schema import (
    ChatRequest,
    ChatHistoryRequest,
    ChatSessionCreateRequest,
    IndexDocumentRequest,
    IndexDocumentResponse,
    RagPreviewRequest,
    RagPreviewResponse,
    RagStatusResponse
)
from backend.services.chat_service import chat_with_ai
from backend.services.workflow_engine import run_workflow_stream

# 路由注册器：集中管理当前模块下的所有接口
router = APIRouter()


@router.post("/chat_history")
def chat_history(request: ChatHistoryRequest):
    """
    返回每个前端模式最近一次数据库会话及其消息，用于刷新后恢复聊天历史。

    :param request: 聊天历史请求对象。包含：mode_names：需要恢复的模式列表
    :return: 每个模式最近一次会话及对应消息列表
    """
    # 加载各模式最近一次数据库会话及其消息
    return {
        "mode_sessions": load_latest_mode_sessions(request.mode_names)
    }


@router.post("/chat_session")
def create_chat_session(request: ChatSessionCreateRequest):
    """
    创建一个空聊天会话，主要用于前端新建或清空当前模式聊天后的状态同步。

    :param request: 会话创建请求对象。包含：session_id、mode、title
    :return: 新创建会话的信息
    """
    # 创建或更新当前会话记录
    ensure_chat_session(
        session_id=request.session_id,
        mode=request.mode,
        title=request.title
    )
    return {
        "session_id": request.session_id,
        "mode": request.mode
    }


@router.delete("/chat_session/{session_id}")
def clear_chat_session(session_id: str):
    """
    删除一个聊天会话及其消息、文档、RAG 查询等级联数据。

    :param session_id: 需要删除的会话ID
    :return: 删除结果提示信息
    """
    # 删除数据库中的会话及所有级联关联数据
    delete_chat_session(session_id)
    # 同时清理当前会话在内存 RAG store 中的文档索引
    clear_document_chunks(session_id)
    return {"message": f"session {session_id} 的聊天数据已清理"}


@router.post("/chat_stream")
def chat_stream(request: ChatRequest):
    """
    聊天流式接口。

    接收前端聊天请求，初始化模型客户端，并调用聊天服务返回 SSE 事件流响应。
    """
    client = get_client()  # 创建统一的大模型客户端
    return chat_with_ai(request, client)  # 将请求交给聊天服务处理


@router.post("/workflow_stream")
def workflow_stream(request: ChatRequest):
    """
    工作流流式接口。

    接收前端工作流请求，初始化模型客户端，并调用工作流服务返回 SSE 事件流响应。
    """
    client = get_client()  # 创建统一的大模型客户端
    return run_workflow_stream(request, client)  # 将请求交给工作流服务处理


@router.post("/index_document", response_model=IndexDocumentResponse)
def index_document(request: IndexDocumentRequest):
    """
    文档索引接口。

    作用：
    1. 接收前端上传并提取后的完整文本
    2. 做文本切块
    3. 存入当前 session 对应的内存存储
    """
    cleaned_text = request.document_text.strip()  # 去掉首尾空白，避免无效输入
    # 如果清理后发现文档内容是空的，就抛出一个 400 错误。阻止无效文档进入 RAG 索引流程。
    if not cleaned_text:
        raise HTTPException(status_code=400, detail="文档内容不能为空。")

    chunks = split_text_into_chunks(cleaned_text) # 将完整文档切分成多个文本块
    # 如果切块结果为空，也认为请求无效，直接报错。防止后面把空索引存进去。
    if not chunks:
        raise HTTPException(status_code=400, detail="文档切块后为空，请检查输入内容。")

    # 把切好的块保存到当前会话对应的 RAG store 里。这样后面同一会话里就能基于这份文档做检索。
    save_document_chunks(
        session_id=request.session_id,
        file_name=request.file_name,
        chunks=chunks
    )

    # 返回索引结果，方便前端展示切块数量
    return IndexDocumentResponse(
        session_id=request.session_id,
        file_name=request.file_name,
        chunk_count=len(chunks)
    )


@router.post("/rag_preview", response_model=RagPreviewResponse)
def rag_preview(request: RagPreviewRequest):
    """
    返回当前 query 命中的 RAG 片段摘要，便于前端展示检索依据。
    """
    # 调用 RAG 服务层，构造适合前端展示的检索片段预览数据
    chunks = build_rag_preview(
        session_id=request.session_id,
        query=request.query,
        top_k=request.top_k
    )

    # 返回当前 query 对应的检索摘要
    return RagPreviewResponse(
        session_id=request.session_id,
        query=request.query,
        chunks=chunks
    )


@router.get("/rag_status/{session_id}", response_model=RagStatusResponse)
def rag_status(session_id: str):
    """
    返回当前 session 的内存 RAG store 生命周期状态。
    """
    # 查询当前会话的文档状态，并转换为响应模型
    return RagStatusResponse(**get_document_status(session_id))


@router.delete("/clear_document/{session_id}")
def clear_document(session_id: str):
    """
    清理某个 session 对应的临时 RAG 文档索引

    作用：
    1. 当前端新建会话或清空聊天时，主动释放 session 的文本块
    2. 避免第一阶段 RAG 的内存存储无限累积
    """
    clear_document_chunks(session_id) # 从 RAG store 中删除该会话对应的文档记录
    return {"message": f"session {session_id} 的文档索引已清理"}
