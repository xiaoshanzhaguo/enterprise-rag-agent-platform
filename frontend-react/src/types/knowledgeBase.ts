/*
 * 知识库相关类型定义。
 *
 * 功能说明：
 * 1. 约束前端从 FastAPI 获取到的知识库、分类、RAG 状态数据结构。
 * 2. 让页面和 API 封装在写字段名时有 TypeScript 提示，减少拼错字段的风险。
 * 3. 这些类型要尽量和后端 schema 保持一致，后端字段变化时优先来这里同步。
 */

// 单个企业知识库的基础信息。
export interface KnowledgeBaseItem {
  knowledge_base_id: string // 知识库 ID，用于提问、上传、查询状态时告诉后端操作哪个知识库。
  name: string // 知识库展示名称，页面下拉框和配置列表里会显示它。
  description?: string | null // 知识库说明，后端可能返回空字符串、null 或不返回，所以这里写成可选。
  owner_role?: string // 默认维护角色，例如 kb_admin，用于后续做权限控制时使用。
  created_at?: string | null // 创建时间，当前页面暂时不展示，但保留字段方便以后扩展。
  updated_at?: string | null // 更新时间，当前页面暂时不展示，但保留字段方便以后扩展。
}

// /knowledge_bases 接口返回的数据结构。
export interface KnowledgeBaseListResponse {
  knowledge_bases: KnowledgeBaseItem[] // 企业知识库列表，前端会用它渲染“知识库范围”下拉框。
}

// 单个知识库分类，例如 HR、财务、IT、产品。
export interface KnowledgeBaseCategory {
  category_id: string // 分类 ID，前端选择后会把它作为当前分类值保存。
  label: string // 分类展示名称，例如“财务”，给用户看的文本。
  knowledge_base_type: string // 后端真正用于过滤 documents.knowledge_base_type 的分类值。
  department?: string | null // 分类对应的部门名称，例如“财务部”，没有时允许为空。
  description?: string // 分类说明，后续可以用于 tooltip 或帮助文本。
}

// /knowledge_base_categories 接口返回的数据结构。
export interface KnowledgeBaseCategoryListResponse {
  categories: KnowledgeBaseCategory[] // 可上传、可检索过滤的分类列表。
}

// RAG 状态里单个文档的摘要信息。
export interface RagStatusDocument {
  document_id?: number // SQLite documents 表里的文档 ID；有些旧数据可能没有，所以可选。
  file_name?: string | null // 文档文件名，用于在前端展示“当前知识库有哪些文件”。
  chunk_count?: number // 当前文档被切成了多少个 chunk，用来判断检索材料是否完整。
  created_at?: string | null // 文档入库时间，当前页面暂时不展示。
  knowledge_base_id?: string | null // 文档所属的企业知识库 ID。
  knowledge_base_type?: string | null // 文档所属分类，例如 finance、it。
  department?: string | null // 文档所属部门，例如 财务部、IT 部。
  process_type?: string | null // 流程类型，例如 reimbursement、permission_apply。
  process_status?: string | null // 流程状态，例如 active、draft。
}

// /rag_status/{session_id} 接口返回的数据结构。
export interface RagStatusResponse {
  session_id: string // 当前查询状态使用的会话 ID；企业知识库模式下它只用于兼容接口路径。
  knowledge_base_id?: string | null // 当前状态对应的企业知识库 ID。
  has_document: boolean // 是否已经有可检索文档。
  file_names: string[] // 当前范围内的文件名列表。
  document_count: number // 当前范围内的文档总数。
  chunk_count: number // 当前范围内的 chunk 总数。
  documents: RagStatusDocument[] // 文档状态明细，右侧“当前检索上下文”会读取它。
  expires_in_seconds: number // 兼容旧前端字段；SQLite 持久化后一般不会真正过期。
}
