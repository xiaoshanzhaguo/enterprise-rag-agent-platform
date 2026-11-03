"""
API 路由模块。

职责：
1. 注册前端可调用的后端接口
2. 提供聊天流式响应接口
3. 提供工作流和轻量 Agent 流式响应接口
4. 提供文档上传、文本切块、RAG 索引构建、检索预览、状态查询与清理接口
5. 提供聊天历史恢复接口
6. 提供最近会话列表、指定会话恢复、会话创建与删除接口
7. 统一作为前端与业务服务层之间的请求入口

说明：
- 当前模块属于 API（接口层）
- 不负责具体业务逻辑实现
- 不负责模型调用细节
- 不负责数据库操作实现
- 聊天、工作流和轻量 Agent 逻辑由 Service 层负责
- 聊天记录、文档索引与检索记录持久化由 Repository 层负责
- 文档切块、检索与状态管理由 RAG 模块负责
- 前端所有业务请求均通过本模块进入系统

当前已提供接口：

聊天相关：
- POST /chat_stream
- POST /workflow_stream
- POST /agent_stream

聊天历史相关：
- POST /chat_history
- GET /chat_sessions
- GET /chat_session/{session_id}
- POST /chat_session
- DELETE /chat_session/{session_id}

RAG 相关：
- POST /index_document
- POST /rag_preview
- GET /rag_status/{session_id}
- DELETE /clear_document/{session_id}

数据存储：
- 聊天记录持久化存储于 SQLite
- 文档索引持久化存储于 SQLite
- RAG 检索记录持久化存储于 SQLite，检索预览会返回引用来源和命中原文片段
"""

from fastapi import APIRouter, Depends, HTTPException, Query

from backend.api.rag_index import ERROR_RESPONSES, get_index_management_service, index_http_response
from backend.services.rag_documents import IndexManagementService, infer_document_business_fields
from backend.llm.client import get_client
from backend.rag.store import clear_document_chunks
from backend.rag.categories import (
    list_knowledge_base_categories,
    normalize_knowledge_base_type,
)
from backend.rag.knowledge_bases import (
    normalize_knowledge_base_id,
)
from backend.rag.service import build_rag_preview, resolve_retrieval_mode
from backend.db.repository import delete_chat_session
from backend.db.repository import rename_chat_session
from backend.db.repository import ensure_chat_session
from backend.db.repository import get_chat_session_detail
from backend.db.repository import list_knowledge_bases
from backend.db.repository import list_recent_chat_sessions
from backend.db.repository import load_latest_mode_sessions
from backend.schema.chat_schema import (
    ChatRequest,
    ChatHistoryRequest,
    ChatSessionDetailResponse,
    ChatSessionListResponse,
    ChatSessionCreateRequest,
    ChatSessionRenameRequest,
    IndexDocumentRequest,
    IndexDocumentResponse,
    KnowledgeBaseCategoryListResponse,
    KnowledgeBaseListResponse,
    RagPreviewRequest,
    RagPreviewResponse,
    RagStatusResponse
)
from backend.services.chat_service import chat_with_ai
from backend.services.workflow_engine import run_workflow_stream
from backend.services.agent_service import run_agent_stream

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


@router.get("/chat_sessions", response_model=ChatSessionListResponse)
def chat_sessions(limit: int = Query(default=10, ge=1, le=50), mode: str | None = None, include_empty: bool = False):
    """
    返回最近更新的聊天会话列表，用于前端侧边栏展示历史会话。

    :param limit: 最多返回多少条会话，默认10条
    :return: 最近会话摘要列表
    """
    # 兼容旧入口默认隐藏空会话；React 显式请求 include_empty。
    sessions = list_recent_chat_sessions(limit=limit, mode=mode, include_empty=include_empty)
    # 按响应模型返回，保证前端拿到稳定字段
    return {
        "sessions": sessions
    }


@router.get("/chat_session/{session_id}", response_model=ChatSessionDetailResponse)
def chat_session_detail(session_id: str):
    """
    返回指定会话详情，用于前端点击历史会话后恢复对应消息。

    :param session_id: 需要恢复的会话ID
    :return: 会话详情与消息列表
    """
    # 从数据库读取指定会话详情
    if session_id.startswith('knowledge_base:'):
        raise HTTPException(status_code=403, detail='公共知识库不是聊天会话。')
    session = get_chat_session_detail(session_id)
    # 会话不存在时返回 404，避免前端误以为恢复成功
    if not session:
        raise HTTPException(status_code=404, detail="会话不存在或已被删除。")

    # 返回会话详情
    return session


@router.post("/chat_session")
def create_chat_session(request: ChatSessionCreateRequest):
    """
    创建一个空聊天会话，主要用于前端新建或清空当前模式聊天后的状态同步。

    :param request: 会话创建请求对象。包含：session_id、mode、title
    :return: 新创建会话的信息
    """
    # 创建或更新当前会话记录
    if not request.session_id.strip() or request.session_id.startswith('knowledge_base:'):
        raise HTTPException(status_code=400, detail='会话编号无效。')
    try:
        ensure_chat_session(session_id=request.session_id, mode=request.mode, title=request.title)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
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
    if session_id.startswith('knowledge_base:'):
        raise HTTPException(status_code=403, detail='不能删除公共知识库会话。')
    delete_chat_session(session_id)
    # 同时清理当前会话在数据库 RAG store 中的文档索引
    clear_document_chunks(session_id)
    return {"message": f"session {session_id} 的聊天数据已清理"}


@router.patch('/chat_session/{session_id}')
def update_chat_session_title(session_id: str, request: ChatSessionRenameRequest):
    if session_id.startswith('knowledge_base:'):
        raise HTTPException(status_code=403, detail='不能修改公共知识库会话。')
    if not rename_chat_session(session_id, request.title):
        raise HTTPException(status_code=404, detail='会话不存在。')
    return {'session_id': session_id, 'title': request.title}


@router.post("/chat_stream")
def chat_stream(request: ChatRequest):
    """
    聊天流式接口。

    接收前端聊天请求，初始化模型客户端，并调用聊天服务返回 SSE 事件流响应。
    """
    client = get_client()  # 创建统一的 LangChain ChatModel，路由和 SSE 返回结构保持不变
    return chat_with_ai(request, client)  # 将请求交给聊天服务处理


@router.post("/workflow_stream")
def workflow_stream(request: ChatRequest):
    """
    工作流流式接口。

    接收前端工作流请求，初始化模型客户端，并调用工作流服务返回 SSE 事件流响应。
    """
    client = get_client()  # 创建统一的 LangChain ChatModel，路由和 SSE 返回结构保持不变
    return run_workflow_stream(request, client)  # 将请求交给工作流服务处理


@router.post("/agent_stream")
def agent_stream(request: ChatRequest):
    """
    企业知识库问答 Agent 流式接口。

    接收前端 Agent 请求，初始化模型客户端，并调用轻量 Agent 服务返回 SSE 事件流响应。
    """
    client = get_client()  # 创建统一的 LangChain ChatModel，路由和 SSE 返回结构保持不变
    return run_agent_stream(request, client)  # 将请求交给轻量 Agent 服务处理


@router.get("/knowledge_base_categories", response_model=KnowledgeBaseCategoryListResponse)
def knowledge_base_categories():
    """
    返回前端可选的知识库分类列表。

    当前项目还没有独立的分类表，因此后端常量是唯一可信来源。
    前端上传文档、检索过滤都应该优先使用这里返回的 category_id /
    knowledge_base_type，避免前后端分类值不一致。
    """
    return {
        "categories": list_knowledge_base_categories()
    }


@router.get("/knowledge_bases", response_model=KnowledgeBaseListResponse)
def knowledge_bases():
    """
    返回可查询的企业知识库列表。

    这个接口是“知识库从 session 中独立出来”的最小后端入口。
    前端提问时会把选中的 knowledge_base_id 一起传回来，
    后端再按该知识库范围检索，而不是只查当前聊天 session 的临时文件。
    """
    return {
        "knowledge_bases": list_knowledge_bases()
    }


@router.post("/index_document", response_model=IndexDocumentResponse, responses={**ERROR_RESPONSES, 202: {"model": IndexDocumentResponse}})
def index_document(request: IndexDocumentRequest, service: IndexManagementService = Depends(get_index_management_service)):
    return index_http_response(lambda: service.upload(request), IndexDocumentResponse)


@router.post("/rag_preview", response_model=RagPreviewResponse)
def rag_preview(request: RagPreviewRequest):
    """
    返回当前 query 命中的 RAG 引用来源和原文片段，便于前端展示检索依据。
    """
    # 检索过滤条件必须先校验。None 表示不过滤分类，也就是检索当前会话全部文档。
    try:
        knowledge_base_type_filter = normalize_knowledge_base_type(
            request.knowledge_base_type_filter,
            allow_none=True,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    # 只有前端显式传入 knowledge_base_id 时才按企业知识库检索；
    # 旧的会话级 RAG 入口不传该字段时，继续按 session_id 检索，保持兼容。
    knowledge_base_id = normalize_knowledge_base_id(request.knowledge_base_id) if request.knowledge_base_id else None

    # 调用 RAG 服务层，构造适合前端展示的检索片段预览数据
    chunks = build_rag_preview(
        session_id=request.session_id,
        query=request.query,
        knowledge_base_type_filter=knowledge_base_type_filter,
        knowledge_base_id=knowledge_base_id,
        top_k=request.top_k
    )

    # 返回当前 query 对应的检索摘要
    return RagPreviewResponse(
        session_id=request.session_id,
        knowledge_base_id=knowledge_base_id,
        query=request.query,
        retrieval_mode=resolve_retrieval_mode(chunks),
        chunks=chunks
    )


@router.get("/rag_status/{session_id}", response_model=RagStatusResponse, responses=ERROR_RESPONSES)
def rag_status(session_id: str, knowledge_base_id: str | None = Query(default=None),
               service: IndexManagementService = Depends(get_index_management_service)):
    return index_http_response(lambda: service.document_status(session_id,
        knowledge_base_id=normalize_knowledge_base_id(knowledge_base_id) if knowledge_base_id else None),
        RagStatusResponse, query=True)


@router.delete("/clear_document/{session_id}")
def clear_document(session_id: str):
    """
    清理某个 session 对应的临时 RAG 文档索引

    作用：
    1. 当前端新建会话或清空聊天时，主动删除 session 的持久化文档和文本块
    2. 避免当前会话继续检索旧文档
    """
    clear_document_chunks(session_id) # 从数据库 RAG store 中删除该会话对应的文档记录
    return {"message": f"session {session_id} 的文档索引已清理"}
