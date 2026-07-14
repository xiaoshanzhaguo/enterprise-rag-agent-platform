/*
 * 聊天和 Agent 请求相关类型定义。
 *
 * 功能说明：
 * 1. 约束员工提问时前端需要保存哪些状态。
 * 2. 约束调用 /agent_stream 时要传给后端的请求体结构。
 * 3. 后续接入 SSE 流式回答时，优先复用这里的 AgentStreamRequest。
 * 4. 当前已经补充 ChatMessage 和 AgentStreamEvent，供聊天窗口和 SSE 解析复用。
 */

// 当前前端先用字符串字面量表示角色，后续如果接真实登录系统，可以从后端返回角色。
export type UserRole = 'employee' | 'kb_admin' | 'approver' | 'admin'

// 聊天输入区的草稿状态。
export interface ChatDraftState {
  question: string // 员工当前输入的问题。
  selectedKnowledgeBaseId: string // 当前选择的企业知识库 ID。
  selectedCategoryId: string // 当前选择的知识库分类 ID，all 表示不过滤分类。
  userRole: UserRole // 当前用户角色，用于后续控制上传、审批等权限。
}

// React 聊天窗口里单条消息的展示模型。
export interface ChatMessage {
  id: string // 前端生成的消息 ID，用于 React 列表 key 和流式更新定位。
  role: 'user' | 'assistant' // 消息角色：user 表示员工问题，assistant 表示 AI 回答。
  content: string // 聊天气泡里展示的正文内容。
  steps?: string[] // Agent 执行步骤，例如“正在判断是否需要知识库”“已命中 3 个片段”。
  status?: 'streaming' | 'complete' | 'error' // 当前 AI 消息状态，用于展示生成中或错误。
}

// Agent 路由判断元数据，对应后端 metadata.agent_route。
export interface AgentRouteMetadata {
  need_knowledge_base?: boolean // Agent 判断本轮是否需要查询知识库。
  route_result?: string // 后端记录的路由结果，例如 use_knowledge_base 或 skip_knowledge_base。
  reason?: string // Agent 判断理由，用于右侧过程面板解释为什么检索或跳过检索。
  rewritten_query?: string // Agent 改写后的检索问题。
  effective_retrieval_query?: string // 最终真正用于检索的 query，可能是改写 query，也可能回退成原问题。
  retrieval_mode?: string // 实际检索方式，例如 vector、keyword、no_hit。
  knowledge_base_id?: string | null // 本轮检索限定的知识库 ID。
  knowledge_base_type_filter?: string | null // 本轮检索限定的知识库分类，例如 finance、it。
}

// 右侧引用来源面板里的单个命中 chunk。
export interface RagPreviewChunk {
  rank?: number // 检索排名，数字越小表示越靠前。
  file_name?: string | null // 来源文件名。
  chunk_id?: number | string | null // 文本块编号。
  score?: number // 相似度或关键词命中分数。
  retrieval_mode?: string // 当前 chunk 的检索方式，例如 vector 或 keyword。
  source?: string // 后端拼好的引用来源，例如 财务制度.md#chunk-3。
  text?: string // chunk 原文，后续做展开详情时可以使用。
  text_preview?: string // chunk 预览文本，右侧面板默认展示这个字段。
  text_length?: number // chunk 原文长度。
  knowledge_base_id?: string | null // chunk 所属知识库 ID。
  knowledge_base_type?: string | null // chunk 所属知识库分类。
  department?: string | null // 业务部门字段。
  process_type?: string | null // 流程类型字段。
  process_status?: string | null // 流程状态字段。
}

// 后端 final 事件里返回的 Agent 元数据。
export interface AgentStreamMetadata {
  agent_route?: AgentRouteMetadata // Agent 判断结果。
  rag_preview_chunks?: RagPreviewChunk[] // 本轮实际命中的引用 chunk 列表。
  rag_status_info?: unknown // 后端附带的知识库状态，当前右侧面板暂时不用展开。
}

// 右侧 Agent 过程面板使用的前端状态。
export interface AgentTracePanelState {
  status: 'idle' | 'running' | 'complete' | 'error' // 当前 Agent 执行状态。idle 表示 空闲状态/ 初始状态 / 尚未开始执行。
  workflowText?: string // workflow_start 事件内容。
  judgeText?: string // 判断是否需要知识库步骤的说明文本。
  retrieveText?: string // 检索证据步骤的说明文本。
  generateText?: string // 生成回答步骤的完成文本，默认不在员工聊天气泡中展示。
  errorMessage?: string // 流式错误信息。
  metadata?: AgentStreamMetadata // final 事件里的结构化元数据。
}

// 后端 SSE 事件结构，对应 backend.schema.chat_schema.StreamEvent。
export interface AgentStreamEvent {
  event_type: 'workflow_start' | 'step_start' | 'delta' | 'step_complete' | 'final' | 'error' // 当前事件类型。
  session_id?: string | null // 当前事件所属会话 ID。
  task_type?: string | null // 当前任务类型，智能问答页一般是 agent。
  step_name?: string | null // 当前步骤名称，例如判断知识库、检索证据、生成回答。
  content?: string // 当前事件携带的文本内容。
  metadata?: AgentStreamMetadata // 后端附带的扩展数据，例如引用片段、路由结果。
  is_final?: boolean // 是否为最后一条事件。
  error_message?: string | null // error 事件里的错误信息。
}

// 调用后端 /agent_stream 接口时的请求体。
export interface AgentStreamRequest {
  session_id: string // 当前聊天会话 ID，用于后端保存消息和检索记录。
  task_type: 'agent' // 当前任务类型。调用 /agent_stream 时固定为 agent。
  input_text: string // 员工提交给 Agent 的原始问题，后端 ChatRequest 使用 input_text 字段。
  mode: string // 当前问答模式；第一版 React 固定为企业知识库问答即可。
  history?: Array<{
    role: 'user' | 'assistant' | 'system' // 历史消息角色。
    content: string // 历史消息内容。
  }> // 历史消息列表；当前先传空数组，后续可接多轮对话。
  user_options: {
    knowledge_base_id?: string // 指定要检索的企业知识库范围。
    knowledge_base_type_filter?: string // 指定分类过滤条件，例如 finance；不传表示让 Agent 自动判断。
    user_role?: UserRole // 当前用户角色，后端可用它判断是否允许上传或管理知识库。
    display_text?: string // 前端展示文本，避免后续附件场景把全文塞进聊天气泡。
  }
  use_rag: boolean // 是否启用 RAG；企业智能问答默认启用。
  rag_top_k: number // 检索返回片段数量，后端限制一般为 1-5。
}
