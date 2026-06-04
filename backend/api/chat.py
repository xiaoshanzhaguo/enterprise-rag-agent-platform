"""
API 路由模块。

职责：
1. 注册前端可调用的后端接口，包括聊天流式接口、工作流流式接口、文档索引接口、RAG 预览接口、RAG 状态接口与文档清理接口
2. 作为前端与业务服务层之间的入口，负责接收请求、做基础校验，并将请求分发到对应的 service 或 rag 模块
3. 统一组织与 RAG 第一阶段相关的接口能力，便于前端进行文档上传、索引、检索预览、状态查询和清理操作

说明：
- 当前模块属于接口层，不直接负责模型生成逻辑，也不直接负责 RAG 检索算法实现
- 聊天与工作流的具体处理逻辑在 service 层
- 文档切块、存储、检索预览与状态查询等能力由 rag 模块提供
- 适合当前项目“前后端分离 + 流式响应 + 第一阶段 RAG” 的工程结构
"""
from fastapi import APIRouter, HTTPException

from backend.llm.client import get_client
from backend.rag.chunker import split_text_into_chunks
from backend.rag.store import save_document_chunks
from backend.rag.store import clear_document_chunks
from backend.rag.store import get_document_status
from backend.rag.service import build_rag_preview
from backend.schema.chat_schema import (
    ChatRequest,
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
