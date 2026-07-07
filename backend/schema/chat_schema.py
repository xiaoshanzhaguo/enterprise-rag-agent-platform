"""
Schema 数据模型模块。

职责：
1. 定义前后端交互时使用的核心数据结构，包括聊天请求、流式事件、文档索引、RAG 引用预览、RAG 状态、聊天历史恢复、会话列表与会话创建等接口模型
2. 通过 Pydantic 模型约束字段类型、默认值和取值范围，保证接口输入输出结构清晰、可校验、可维护
3. 统一管理聊天、工作流、轻量 Agent、RAG 检索增强、SQLite 历史持久化相关的数据协议
4. 作为 API 层、Service 层、Repository 层和前端之间的“数据契约”

说明：
- 当前模块属于 schema / 协议层，不直接处理业务逻辑
- 主要作用是统一接口字段结构，避免前后端联调时出现字段不一致
- ChatRequest 用于聊天、工作流和轻量 Agent 请求
- StreamEvent 用于 SSE 流式响应协议
- IndexDocumentRequest / IndexDocumentResponse 用于文档索引接口
- RagPreviewRequest / RagPreviewResponse / RagStatusResponse 用于 RAG 检索引用预览与状态查询
- ChatHistoryRequest / ChatSessionSummary / ChatSessionDetailResponse / ChatSessionCreateRequest 用于聊天历史恢复和会话管理
- 适合当前项目“流式输出 + 多模式内容处理 + 可解释 RAG + SQLite 历史持久化”的工程结构
"""

from pydantic import BaseModel, Field
from typing import Optional, Literal, TypeAlias, List, Dict, Any

# 消息角色类型：限定只能是 system / user / assistant
MessageRole: TypeAlias = Literal["system", "user", "assistant"]

# 任务类型：限定当前项目支持的任务模式
TaskType: TypeAlias = Literal["chat", "summary", "rewrite", "translate", "workflow", "agent"]

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
    mode: str = "default"   # 当前功能模式，例如企业知识库问答、内容分析、结构优化等
    history: List[MessageItem] = Field(default_factory=list)  # 历史消息列表
    user_options: Dict[str, Any] = Field(default_factory=dict)  # 扩展参数，如语气、长度、语言等

    # RAG 检索增强字段
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
    metadata: Dict[str, Any] = Field(default_factory=dict)  # 当前事件携带的扩展元数据，例如 Agent 最终返回的 RAG 引用片段
    is_final: bool = False  # 是否为最后一条流式消息
    error_message: Optional[str] = None  # 错误信息，仅在 error 事件中使用


class IndexDocumentRequest(BaseModel):
    """
    文档索引请求模型。

    用于接收前端上传并提取后的完整文本，交给后端切块并追加到当前会话索引。
    """
    session_id: str  # 当前会话 ID
    document_text: str  # 完整文档文本
    file_name: Optional[str] = None  # 文件名，可选
    knowledge_base_id: Optional[str] = None  # 文档要写入的企业知识库 ID；不传时使用默认企业知识库
    user_role: Optional[str] = None  # 演示级用户角色；kb_admin/admin 才允许上传知识库
    knowledge_base_type: Optional[str] = None  # 知识库类型，例如 hr、finance、it、product、general；不传时由后端推断
    department: Optional[str] = None  # 所属部门，例如 HR、财务、IT、产品
    process_type: Optional[str] = None  # 流程类型，例如 leave、reimbursement、vpn、gitlab_access
    process_status: Optional[str] = None  # 流程状态，例如 active、draft、archived；不传时默认为 active


class IndexDocumentResponse(BaseModel):
    """
    文档索引响应模型。
    """
    session_id: str  # 当前会话 ID
    knowledge_base_id: str = "session_scoped"  # 本次文档写入的知识库范围；旧入口为 session_scoped
    file_name: Optional[str] = None  # 文件名
    chunk_count: int  # 文档切分后的文本块数量
    knowledge_base_type: str = "general"  # 本次索引文档的知识库类型
    department: Optional[str] = None  # 本次索引文档所属部门
    process_type: Optional[str] = None  # 本次索引文档流程类型
    process_status: str = "active"  # 本次索引文档流程状态


class KnowledgeBaseCategory(BaseModel):
    """
    前端可选择的知识库分类模型。

    当前项目里 category_id 和 knowledge_base_type 暂时使用同一个值，
    例如 finance 表示“财务”分类。后续如果新增独立分类表，可以继续保留
    category_id 作为前后端交互字段，再在后端映射到数据库主键。
    """
    category_id: str  # 前端选择后提交的分类 ID
    label: str  # 前端下拉框展示名
    knowledge_base_type: str  # 当前文档入库和检索过滤使用的分类值
    department: Optional[str] = None  # 分类对应的部门展示名
    description: str = ""  # 分类说明，便于以后前端 tooltip 或说明面板使用


class KnowledgeBaseCategoryListResponse(BaseModel):
    """
    知识库分类列表响应模型。
    """
    categories: List[KnowledgeBaseCategory]  # 可上传、可检索过滤的分类列表


class KnowledgeBaseItem(BaseModel):
    """
    企业知识库列表项。

    这是“知识库从 session 独立出来”的接口模型：
    - knowledge_base_id 表示检索和上传的业务范围
    - session_id 只表示一次聊天会话，不再代表知识库本身
    """
    knowledge_base_id: str  # 知识库 ID
    name: str  # 知识库展示名称
    description: Optional[str] = None  # 知识库说明
    owner_role: str = "kb_admin"  # 默认维护角色
    created_at: Optional[str] = None  # 创建时间
    updated_at: Optional[str] = None  # 更新时间


class KnowledgeBaseListResponse(BaseModel):
    """
    企业知识库列表响应模型。
    """
    knowledge_bases: List[KnowledgeBaseItem]  # 当前可查询的企业知识库列表


class RagPreviewRequest(BaseModel):
    """
    RAG 检索预览请求。
    """
    session_id: str  # 当前会话 ID
    query: str  # 当前查询问题
    knowledge_base_id: Optional[str] = None  # 本次检索使用的企业知识库 ID
    knowledge_base_type_filter: Optional[str] = None # 知识库分类
    top_k: int = Field(default=3, ge=1, le=5)  # 检索预览的片段数量


class RagPreviewChunk(BaseModel):
    """
    前端可视化展示用的检索片段摘要。
    """
    rank: int | None = None  # 检索排序，数字越小表示越靠前
    file_name: Optional[str] = None  # 命中文本块所属文件名
    chunk_id: int | None = None  # 文本块编号
    score: float = 0.0  # 检索分数；关键词模式为整数，向量模式为相似度小数
    retrieval_mode: str = "unknown"  # 当前命中片段实际使用的检索方式，例如 vector、keyword 或 no_hit
    source: Optional[str] = None  # 引用来源标识，例如：员工手册.md#chunk-4
    text: str = ""  # 命中的原文片段
    text_preview: str  # 文本预览内容
    text_length: int  # 原始文本总长度
    knowledge_base_id: Optional[str] = None  # 命中文档所属企业知识库 ID
    knowledge_base_type: Optional[str] = None  # 来源文档的知识库类型
    department: Optional[str] = None  # 来源文档所属部门
    process_type: Optional[str] = None  # 来源文档流程类型
    process_status: Optional[str] = None  # 来源文档流程状态


class RagPreviewResponse(BaseModel):
    """
    RAG 检索预览响应。
    """
    session_id: str  # 当前会话 ID
    knowledge_base_id: Optional[str] = None  # 本次预览使用的企业知识库 ID
    query: str  # 当前查询问题
    retrieval_mode: str = "unknown"  # 本次预览实际使用的检索方式，例如 vector、keyword 或 no_hit
    chunks: List[RagPreviewChunk]  # 检索片段摘要列表


class RagStatusResponse(BaseModel):
    """
    RAG 数据库文档状态响应。
    """
    session_id: str  # 当前会话 ID
    knowledge_base_id: Optional[str] = None  # 当前状态对应的企业知识库 ID
    has_document: bool  # 当前会话是否已有索引文档
    file_names: List[str] = Field(default_factory=list)  # 当前会话已索引的全部文档文件名
    document_count: int = 0  # 当前会话已索引的文档数量
    chunk_count: int = 0  # 当前会话全部文档的文本块总数量
    documents: List[Dict[str, Any]] = Field(default_factory=list)  # 当前会话每份文档的状态明细
    expires_in_seconds: int = 0  # 数据库持久化后默认不过期，保留该字段兼容前端展示


class ChatHistoryRequest(BaseModel):
    """
    前端刷新后恢复历史时使用的请求模型。
    """
    mode_names: List[str] # 需要恢复历史会话的前端模式名称列表


class ChatSessionSummary(BaseModel):
    """
    侧边栏历史会话摘要模型。

    用于展示最近会话列表，不包含完整消息内容。
    """
    session_id: str  # 会话 ID
    mode: str  # 会话所属模式
    title: str = "未命名会话"  # 会话标题
    created_at: str  # 会话创建时间
    updated_at: str  # 会话最后更新时间
    message_count: int = 0  # 会话消息数量


class ChatSessionListResponse(BaseModel):
    """
    最近会话列表响应模型。

    用于前端侧边栏展示最近 10 条历史会话。
    """
    sessions: List[ChatSessionSummary]  # 最近会话摘要列表


class ChatSessionDetailResponse(BaseModel):
    """
    指定会话详情响应模型。

    用于点击历史会话后恢复对应消息。
    """
    session_id: str  # 会话 ID
    mode: str  # 会话所属模式
    title: Optional[str] = None  # 会话标题
    created_at: str  # 会话创建时间
    updated_at: str  # 会话最后更新时间
    messages: List[Dict[str, Any]] = Field(default_factory=list)  # 会话下的全部消息


class ChatSessionCreateRequest(BaseModel):
    """
    前端新建空会话时使用的请求模型。
    """
    session_id: str  # 新会话 ID
    mode: str  # 当前会话所属模式
    title: Optional[str] = None  # 会话标题，可选
