/*
 * 知识库相关接口封装。
 *
 * 功能说明：
 * 1. 把页面里需要调用的知识库接口集中放在这里。
 * 2. 页面只关心“我要获取知识库列表”，不用重复写 fetch 路径。
 * 3. 如果后端接口路径变化，优先改这里，不要到页面里到处找。
 */

// 引入统一 GET / POST 请求函数，避免每个接口重复写 fetch 和错误处理。
import { apiGet, apiPost } from './httpClient'

// 引入接口返回值类型，让调用方能获得字段提示。
import type {
  IndexDocumentRequest,
  IndexDocumentResponse,
  KnowledgeBaseCategoryListResponse,
  KnowledgeBaseListResponse,
  RagStatusResponse,
} from '../types/knowledgeBase'

// 获取企业知识库列表，用于渲染“知识库范围”下拉框。
export function fetchKnowledgeBases() {
  // 后端返回结构是 { knowledge_bases: [...] }。
  return apiGet<KnowledgeBaseListResponse>('/knowledge_bases')
}

// 获取知识库分类列表，用于渲染 HR / 财务 / IT / 产品等分类选项。
export function fetchKnowledgeBaseCategories() {
  // 后端返回结构是 { categories: [...] }。
  return apiGet<KnowledgeBaseCategoryListResponse>('/knowledge_base_categories')
}

// 上传并索引企业知识库文档。
export function indexKnowledgeBaseDocument(request: IndexDocumentRequest) {
  // 后端 /index_document 接收 JSON，不是 multipart；前端需要先把文件读成 document_text。
  // <IndexDocumentRequest, IndexDocumentResponse> 不是普通参数，而是 TypeScript 泛型参数，它们只是 TypeScript 编译阶段用来检查类型的，不会作为真实代码传到浏览器运行时。
  // 它的意思是：告诉 apiPost 这次请求体的类型是 IndexDocuemntRequest, 这次后端响应的类型是 IndexDocumentResponse。
  return apiPost<IndexDocumentRequest, IndexDocumentResponse>('/index_document', request)
}

// 获取某个 session 或企业知识库下的 RAG 文档状态。
export function fetchRagStatus(sessionId: string, knowledgeBaseId?: string) {
  // URLSearchParams 用来安全拼接 query string，避免手动字符串拼接出错。
  const params = new URLSearchParams()

  // 如果传入 knowledgeBaseId，就让后端按企业知识库范围查询状态。
  if (knowledgeBaseId) {
    // 在 query string 里设置一个参数，参数名是 knowledge_base_id, 参数值是 knowledgeBaseId
    params.set('knowledge_base_id', knowledgeBaseId)
  }

  // 把 query 参数转换成字符串，例如 knowledge_base_id=enterprise_default。
  // params 之前是一个 URLSearchParams 对象
  const queryString = params.toString()

  // 如果有 query 参数就拼到路径后面，没有就只访问基础路径。
  const path = `/rag_status/${sessionId}${queryString ? `?${queryString}` : ''}`

  // 返回类型是 RagStatusResponse，页面可以直接读取 document_count、chunk_count 等字段。
  return apiGet<RagStatusResponse>(path)
}
