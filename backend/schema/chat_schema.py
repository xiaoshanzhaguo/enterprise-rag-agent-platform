"""
Schema 数据模型模块。

职责：
1. 定义前后端交互时使用的核心数据结构，包括聊天请求、流式事件、文档索引、RAG 引用预览、RAG 状态、聊天历史恢复与会话创建等接口模型
2. 通过 Pydantic 模型约束字段类型、默认值和取值范围，保证接口输入输出结构清晰、可校验、可维护
3. 统一管理聊天、工作流、RAG 第一阶段、SQLite 历史持久化相关的数据协议
4. 作为 API 层、Service 层、Repository 层和前端之间的“数据契约”

说明：
- 当前模块属于 schema / 协议层，不直接处理业务逻辑
- 主要作用是统一接口字段结构，避免前后端联调时出现字段不一致
- ChatRequest 用于聊天和工作流请求
- StreamEvent 用于 SSE 流式响应协议
- IndexDocumentRequest / IndexDocumentResponse 用于文档索引接口
- RagPreviewRequest / RagPreviewResponse / RagStatusResponse 用于 RAG 检索引用预览与状态查询
- ChatHistoryRequest / ChatSessionCreateRequest 用于聊天历史恢复和会话管理
- 适合当前项目“流式输出 + 多模式内容处理 + 第一阶段 RAG + SQLite 历史持久化”的工程结构
"""

from pydantic import BaseModel, Field
from typing import Optional, Literal, TypeAlias, List, Dict, Any

# 消息角色类型：限定只能是 system / user / assistant
MessageRole: TypeAlias = Literal["system", "user", "assistant"]

# 任务类型：限定当前项目支持的任务模式
TaskType: TypeAlias = Literal["chat", "summary", "rewrite", "translate", "workflow"]

# 流式事件类型：限定 SSE 输出中允许出现的事件名称
StreamEventType: TypeAlias = Literal[
    "workflow_start",   # 整个工作流开始
    "step_start",       # 某个步骤开始
    "delta",            # 生成一小段增量内容
    "step_complete",    # 某个步骤完成
    "final",            # 整个任务结束
    "error"             # 发生错误
]

class MessageItem(BaseModel):
    """单条消息模型。"""
    role: MessageRole  # 消息角色
    content: str  # 消息内容


class ChatRequest(BaseModel):
    """
    AI 内容任务请求体模型。

    用于描述一次完整的 AI 内容处理请求，
    包括当前输入、任务类型、历史上下文和扩展参数。
    """
    session_id: Optional[str] = None  # 会话 ID，用于区分不同对话
    task_type: TaskType = "chat"  # 当前任务类型
    input_text: str  # 用户本次输入内容
    persona: str = "default"   # 助手人设或内容风格标识
    history: List[MessageItem] = Field(default_factory=list)  # 历史消息列表
    user_options: Dict[str, Any] = Field(default_factory=dict)  # 扩展参数，如语气、长度、语言等

    # RAG 第一阶段新增字段
    use_rag: bool = False # 是否启用检索增强
    rag_top_k: int = Field(default=3, ge=1, le=5) # 检索返回的片段数量


class StreamEvent(BaseModel):
    """
    流式事件模型。

    用于后端在流式输出过程中，向前端持续发送事件消息。
    前端可根据事件类型更新界面状态、拼接文本内容或处理异常。
    """
    event_type: StreamEventType  # 当前流式事件的类型
    session_id: Optional[str] = None  # 当前事件所属的会话ID
    task_type: Optional[TaskType] = None  # 当前任务类型
    step_name: Optional[str] = None  # 当前事件关联的步骤名称
    content: str = ""  # 当前事件携带的文本内容
    is_final: bool = False  # 是否为最后一条流式消息
    error_message: Optional[str] = None  # 错误信息，仅在 error 事件中使用


class IndexDocumentRequest(BaseModel):
    """
    文档索引请求模型。

    用于接收前端上传并提取后的完整文本，交给后端切块并建立临时索引。
    """
    session_id: str  # 当前会话 ID
    document_text: str  # 完整文档文本
    file_name: Optional[str] = None  # 文件名，可选


class IndexDocumentResponse(BaseModel):
    """
    文档索引响应模型。
    """
    session_id: str  # 当前会话 ID
    file_name: Optional[str] = None  # 文件名
    chunk_count: int  # 文档切分后的文本块数量


class RagPreviewRequest(BaseModel):
    """
    RAG 检索预览请求。
    """
    session_id: str  # 当前会话 ID
    query: str  # 当前查询问题
    top_k: int = Field(default=3, ge=1, le=5)  # 检索预览的片段数量


class RagPreviewChunk(BaseModel):
    """
    前端可视化展示用的检索片段摘要。
    """
    file_name: Optional[str] = None  # 命中文本块所属文件名
    chunk_id: int | None = None  # 文本块编号
    score: float = 0.0  # 检索分数；关键词模式为整数，向量模式为相似度小数
    source: Optional[str] = None  # 引用来源标识，例如：员工手册.md#chunk-4
    text: str = ""  # 命中的原文片段
    text_preview: str  # 文本预览内容
    text_length: int  # 原始文本总长度


class RagPreviewResponse(BaseModel):
    """
    RAG 检索预览响应。
    """
    session_id: str  # 当前会话 ID
    query: str  # 当前查询问题
    chunks: List[RagPreviewChunk]  # 检索片段摘要列表


class RagStatusResponse(BaseModel):
    """
    RAG 数据库文档状态响应。
    """
    session_id: str  # 当前会话 ID
    has_document: bool  # 当前会话是否已有索引文档
    file_name: Optional[str] = None  # 当前文档文件名
    chunk_count: int = 0  # 当前文档块数量
    expires_in_seconds: int = 0  # 数据库持久化后默认不过期，保留该字段兼容前端展示


class ChatHistoryRequest(BaseModel):
    """
    前端刷新后恢复历史时使用的请求模型。
    """
    mode_names: List[str] # 需要恢复历史会话的前端模式名称列表


class ChatSessionCreateRequest(BaseModel):
    """
    前端新建空会话时使用的请求模型。
    """
    session_id: str  # 新会话 ID
    mode: str  # 当前会话所属模式
    title: Optional[str] = None  # 会话标题，可选
