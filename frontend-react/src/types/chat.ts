/*
 * 聊天和 Agent 请求相关类型定义。
 *
 * 功能说明：
 * 1. 约束员工提问时前端需要保存哪些状态。
 * 2. 约束调用 /agent_stream 时要传给后端的请求体结构。
 * 3. 后续接入 SSE 流式回答时，优先复用这里的 AgentStreamRequest。
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

// 调用后端 /agent_stream 接口时的请求体。
export interface AgentStreamRequest {
  session_id: string // 当前聊天会话 ID，用于后端保存消息和检索记录。
  question: string // 员工提交给 Agent 的原始问题。
  mode: string // 当前问答模式；第一版 React 固定为企业知识库问答即可。
  user_options: {
    knowledge_base_id?: string // 指定要检索的企业知识库范围。
    knowledge_base_type_filter?: string // 指定分类过滤条件，例如 finance；不传表示让 Agent 自动判断。
    user_role?: UserRole // 当前用户角色，后端可用它判断是否允许上传或管理知识库。
  }
}
