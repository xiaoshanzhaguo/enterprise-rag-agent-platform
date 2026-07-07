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

from fastapi import APIRouter, HTTPException, Query

from backend.llm.client import get_client
from backend.rag.chunker import split_text_into_chunks
from backend.rag.store import save_document_chunks
from backend.rag.store import clear_document_chunks
from backend.rag.store import get_document_status
from backend.rag.categories import (
    DEFAULT_KNOWLEDGE_BASE_TYPE,
    get_department_for_knowledge_base_type,
    list_knowledge_base_categories,
    normalize_knowledge_base_type,
)
from backend.rag.knowledge_bases import (
    SESSION_SCOPED_KNOWLEDGE_BASE_ID,
    can_manage_knowledge_base,
    normalize_knowledge_base_id,
)
from backend.rag.service import build_rag_preview, resolve_retrieval_mode
from backend.db.repository import delete_chat_session
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


def infer_document_business_fields(file_name: str | None, document_text: str) -> dict[str, str | None]:
    """
    根据文件名和文档内容推断企业知识库业务标签。

    为什么要做自动推断：
    1. 当前前端上传入口还没有部门、流程类型下拉框。
    2. 目前需要先把“知识库类型、部门、流程类型、流程状态”字段落到接口和数据库。
    3. 自动推断可以让旧前端请求不改 UI 也能写入基本业务标签，后续再做筛选或下拉选择会更平滑。

    :param file_name: 上传文件名
    :param document_text: 文档正文
    :return: 包含 knowledge_base_type、department、process_type、process_status 的字典
    """
    # 统一转小写，方便同时匹配英文文件名和中文正文关键词
    searchable_text = f"{file_name or ''}\n{document_text[:500]}".lower()

    # 先按部门/知识库类型做粗分类
    if any(keyword in searchable_text for keyword in ["hr", "请假", "考勤", "入职", "试用期"]):
        knowledge_base_type = "hr"
        department = "HR"
    elif any(keyword in searchable_text for keyword in ["finance", "财务", "报销", "差旅", "付款", "发票"]):
        knowledge_base_type = "finance"
        department = "财务"
    elif any(keyword in searchable_text for keyword in ["it", "vpn", "gitlab", "设备", "数据安全"]):
        knowledge_base_type = "it"
        department = "IT"
    elif any(keyword in searchable_text for keyword in ["product", "产品", "发布", "客户反馈", "faq"]):
        knowledge_base_type = "product"
        department = "产品"
    else:
        knowledge_base_type = "general"
        department = None

    # 再按具体流程关键词细分，便于后续按流程类型筛选或路由
    process_type = None
    if any(keyword in searchable_text for keyword in ["请假", "休假", "leave"]):
        process_type = "leave"
    elif any(keyword in searchable_text for keyword in ["考勤", "远程办公", "attendance"]):
        process_type = "attendance"
    elif any(keyword in searchable_text for keyword in ["入职", "onboarding"]):
        process_type = "onboarding"
    elif any(keyword in searchable_text for keyword in ["报销", "reimbursement"]):
        process_type = "reimbursement"
    elif any(keyword in searchable_text for keyword in ["差旅", "travel"]):
        process_type = "travel"
    elif any(keyword in searchable_text for keyword in ["付款", "供应商", "payment"]):
        process_type = "payment"
    elif "vpn" in searchable_text:
        process_type = "vpn"
    elif "gitlab" in searchable_text:
        process_type = "gitlab_access"
    elif any(keyword in searchable_text for keyword in ["设备", "数据安全", "security"]):
        process_type = "device_security"
    elif any(keyword in searchable_text for keyword in ["产品 faq", "faq"]):
        process_type = "product_faq"
    elif any(keyword in searchable_text for keyword in ["发布", "release"]):
        process_type = "release"
    elif any(keyword in searchable_text for keyword in ["客户反馈", "feedback"]):
        process_type = "customer_feedback"

    # 样例文档默认都是已生效制度/流程；以后草稿文档可由前端显式传 draft
    return {
        "knowledge_base_type": knowledge_base_type,
        "department": department,
        "process_type": process_type,
        "process_status": "active",
    }


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
def chat_sessions(limit: int = Query(default=10, ge=1, le=50)):
    """
    返回最近更新的聊天会话列表，用于前端侧边栏展示历史会话。

    :param limit: 最多返回多少条会话，默认10条
    :return: 最近会话摘要列表
    """
    # 读取最近更新的会话摘要；Repository 会过滤空会话
    sessions = list_recent_chat_sessions(limit=limit)
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
    # 同时清理当前会话在数据库 RAG store 中的文档索引
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


@router.post("/agent_stream")
def agent_stream(request: ChatRequest):
    """
    企业知识库问答 Agent 流式接口。

    接收前端 Agent 请求，初始化模型客户端，并调用轻量 Agent 服务返回 SSE 事件流响应。
    """
    client = get_client()  # 创建统一的大模型客户端
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


@router.post("/index_document", response_model=IndexDocumentResponse)
def index_document(request: IndexDocumentRequest):
    """
    文档索引接口。

    作用：
    1. 接收前端上传并提取后的完整文本
    2. 做文本切块
    3. 追加存入选中的企业知识库，而不是只挂在当前聊天 session 下
    """
    # 当前项目还没有正式登录/RBAC，这里先用前端传入的 user_role 做演示级边界。
    # 只有显式写入企业知识库时才校验管理员；旧的 session 临时文档入口保持兼容。
    if request.knowledge_base_id and not can_manage_knowledge_base(request.user_role):
        raise HTTPException(status_code=403, detail="当前角色没有知识库上传权限，请切换为知识库管理员。")

    # 传了 knowledge_base_id 表示写企业公共知识库；没传则保持旧的 session 文档上传行为。
    knowledge_base_id = normalize_knowledge_base_id(request.knowledge_base_id) if request.knowledge_base_id else None

    cleaned_text = request.document_text.strip()  # 去掉首尾空白，避免无效输入
    # 如果清理后发现文档内容是空的，就抛出一个 400 错误。阻止无效文档进入 RAG 索引流程。
    if not cleaned_text:
        raise HTTPException(status_code=400, detail="文档内容不能为空。")

    chunks = split_text_into_chunks(cleaned_text) # 将完整文档切分成多个文本块
    # 如果切块结果为空，也认为请求无效，直接报错。防止后面把空索引存进去。
    if not chunks:
        raise HTTPException(status_code=400, detail="文档切块后为空，请检查输入内容。")

    # 如果前端没有显式传业务标签，就根据文件名和正文做一次轻量推断
    inferred_fields = infer_document_business_fields(
        file_name=request.file_name,
        document_text=cleaned_text,
    )
    # 先校验前端显式传入的分类。传入未知值时直接返回 400，
    # 这样比静默写入错误分类更容易排查“为什么按分类检索不到”的问题。
    try:
        requested_knowledge_base_type = normalize_knowledge_base_type(
            request.knowledge_base_type,
            allow_none=True,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    # 显式传入的分类优先级最高；没有传时使用轻量推断；仍为空则归到 general。
    try:
        knowledge_base_type = normalize_knowledge_base_type(
            requested_knowledge_base_type
            or inferred_fields["knowledge_base_type"]
            or DEFAULT_KNOWLEDGE_BASE_TYPE
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    # department 只是展示字段。优先使用前端传入值；没有传时根据分类补齐；
    # 如果分类是 general，再使用旧的自动推断值兜底。
    department = (
        request.department
        or get_department_for_knowledge_base_type(knowledge_base_type)
        or inferred_fields["department"]
    )
    process_type = request.process_type or inferred_fields["process_type"]
    process_status = request.process_status or inferred_fields["process_status"] or "active"

    # 把切好的块追加保存到当前会话对应的数据库 RAG store。这样后面同一会话里就能基于多份文档做检索。
    save_document_chunks(
        session_id=request.session_id,
        file_name=request.file_name,
        chunks=chunks,
        knowledge_base_id=knowledge_base_id,
        knowledge_base_type=knowledge_base_type,
        department=department,
        process_type=process_type,
        process_status=process_status,
    )

    # 返回索引结果，方便前端展示切块数量
    return IndexDocumentResponse(
        session_id=request.session_id,
        knowledge_base_id=knowledge_base_id or SESSION_SCOPED_KNOWLEDGE_BASE_ID,
        file_name=request.file_name,
        chunk_count=len(chunks),
        knowledge_base_type=knowledge_base_type,
        department=department,
        process_type=process_type,
        process_status=process_status,
    )


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


@router.get("/rag_status/{session_id}", response_model=RagStatusResponse)
def rag_status(session_id: str, knowledge_base_id: str | None = Query(default=None)):
    """
    返回 RAG 文档状态。

    如果传入 knowledge_base_id，则返回企业公共知识库状态；
    如果不传，则兼容旧逻辑，返回当前 session 的临时文档状态。
    """
    # 查询当前知识库或当前会话的文档状态，并转换为响应模型
    # **data: 把 data 字典里的 key 当成参数名; 把 data 字典里的 value 当成参数值。即："name": "Vera" → name="Vera"；"age": 25 → age=25
    return RagStatusResponse(**get_document_status(
        session_id,
        knowledge_base_id=normalize_knowledge_base_id(knowledge_base_id) if knowledge_base_id else None,
    ))


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
