import { ApiError, formatApiError } from './httpClient'
import type {
  IndexDocumentRequest, IndexDocumentResponse, IndexOperationResponse, IndexOperationScope,
  RagStatusResponse, VectorIndexStatus,
} from '../types/knowledgeBase'

type Receipt = IndexDocumentResponse | IndexOperationResponse
type Action = 'resume' | 'retry'
export interface KnowledgeIndexApi {
  fetchRagStatus: (session: string, kb?: string, signal?: AbortSignal) => Promise<RagStatusResponse>
  fetchIndexOperation: (id: string, scope: IndexOperationScope, signal?: AbortSignal) => Promise<IndexOperationResponse>
  indexKnowledgeBaseDocument: (request: IndexDocumentRequest, signal?: AbortSignal) => Promise<IndexDocumentResponse>
  resumeIndexOperation: (id: string, version: number, scope: IndexOperationScope, signal?: AbortSignal) => Promise<IndexOperationResponse>
  retryIndexOperation: (id: string, version: number, scope: IndexOperationScope, signal?: AbortSignal) => Promise<IndexOperationResponse>
}

export function indexPresentation(index?: VectorIndexStatus | null, uncertain = false) {
  if (uncertain) return { label: '状态未确认', tone: 'status-timeout', detail: '请查询最新状态，暂不重复执行。', action: null }
  switch (index?.status) {
    case 'not_requested': return { label: '未启用向量索引', tone: 'index-status-neutral', detail: '文本已保存，可用于关键词检索。', action: null }
    case 'ready': return { label: '索引成功', tone: 'status-simulated', detail: '', action: null }
    case 'pending': return {
      label: index.execution_claimed ? '索引中' : '待执行', tone: 'status-running',
      detail: index.execution_claimed ? '正在执行，仅可查询进度。' : index.resumable ? '文本已保存，可继续执行向量索引。' : '当前配置暂不支持继续执行。',
      action: !index.execution_claimed && index.resumable ? 'resume' as const : null,
    }
    case 'failed': return {
      label: '索引失败', tone: 'status-failed',
      detail: (index.error_message || '向量索引未完成。') + (!index.retryable ? ' 当前配置暂不支持重试。' : ''),
      action: !index.execution_claimed && index.retryable ? 'retry' as const : null,
    }
    default: return { label: '索引状态未知', tone: 'index-status-neutral', detail: '历史文档未记录索引任务。', action: null }
  }
}

function errorReceipt(error: unknown): Receipt | null {
  if (!(error instanceof ApiError) || !error.payload || typeof error.payload !== 'object' || !('receipt' in error.payload)) return null
  const receipt = error.payload.receipt
  if (!receipt || typeof receipt !== 'object' || !('document_id' in receipt) || !('state_confirmed' in receipt)) return null
  return receipt as Receipt
}

interface IndexState {
  status: RagStatusResponse | null
  upload: IndexDocumentResponse | null
  loading: boolean
  uploading: boolean
  busy: string[]
  uncertain: string[]
  uncertainDocuments: Record<number, string>
  uploadUncertain: boolean
  error: string
  queryError: string
}

// 可取消的单个 GET 轮询器；POST 只由显式操作触发。由页面订阅，不依赖 React 或 DOM。
export class KnowledgeIndexController {
  private state: IndexState = { status: null, upload: null, loading: false, uploading: false, busy: [], uncertain: [], uncertainDocuments: {}, uploadUncertain: false, error: '', queryError: '' }
  private listeners = new Set<() => void>()
  private generation = 0
  private revision = 0
  private active = false
  private abort = new AbortController()
  private timer: ReturnType<typeof setTimeout> | undefined
  private read: Promise<void> | undefined
  private locks = new Set<string>()
  private unavailable = new Set<string>()
  private errorOperation: string | null = null
  private polling = false
  private readRevision = 0
  private readonly scope: IndexOperationScope
  private readonly api: KnowledgeIndexApi
  private readonly schedule: { set: (callback: () => void) => ReturnType<typeof setTimeout>; clear: (id: ReturnType<typeof setTimeout>) => void }

  constructor(scope: IndexOperationScope, api: KnowledgeIndexApi,
    schedule = { set: (callback: () => void) => setTimeout(callback, 3000), clear: (id: ReturnType<typeof setTimeout>) => clearTimeout(id) }) {
    this.scope = scope
    this.api = api
    this.schedule = schedule
  }

  getSnapshot = () => this.state
  subscribe = (listener: () => void) => { this.listeners.add(listener); return () => { this.listeners.delete(listener) } }
  private update(patch: Partial<IndexState>) {
    this.state = { ...this.state, ...patch }
    this.listeners.forEach(listener => listener())
  }
  private valid(generation: number) { return this.active && generation === this.generation }
  private signal() { return AbortSignal.any([this.abort.signal, AbortSignal.timeout(15_000)]) }

  start() {
    this.active = true
    this.abort = new AbortController()
    if (this.scope.knowledge_base_id) void this.refresh()
  }
  dispose() {
    this.active = false
    this.generation++
    this.abort.abort()
    if (this.timer !== undefined) this.schedule.clear(this.timer)
    this.timer = undefined
    this.read = undefined
  }
  clearUpload() { this.update({ upload: null, uploadUncertain: false, error: '' }) }

  private idsToPoll() {
    const ids = new Set(this.state.uncertain)
    this.state.status?.documents.forEach(doc => {
      if (doc.vector_index?.status === 'pending' && doc.vector_index.operation_id) ids.add(doc.vector_index.operation_id)
    })
    const index = this.state.upload?.vector_index
    if (index?.status === 'pending' && index.operation_id) ids.add(index.operation_id)
    return [...ids].filter(id => !this.unavailable.has(id))
  }
  private planPoll() {
    if (this.polling) return
    if (this.timer !== undefined) this.schedule.clear(this.timer)
    this.timer = undefined
    if (this.active && this.idsToPoll().length) this.timer = this.schedule.set(() => {
      this.timer = undefined
      void this.poll()
    })
  }
  private async poll() {
    this.polling = true
    await Promise.all(this.idsToPoll().map(id => this.query(id)))
    this.polling = false
    this.planPoll()
  }

  refreshAfterDocumentChange = (): Promise<void> => {
    // 删除由独立控制器执行；使此前的列表 GET 失效，再读取变更后的列表。
    this.revision++
    return this.refresh()
  }

  refresh = (): Promise<void> => {
    if (!this.active) return Promise.resolve()
    if (this.read) return this.readRevision === this.revision ? this.read : this.read.then(() => this.refresh())
    const generation = this.generation, revision = this.revision
    this.readRevision = revision
    this.update({ loading: true })
    const read = (async () => {
      try {
        const status = await this.api.fetchRagStatus(this.scope.session_id, this.scope.knowledge_base_id, this.signal())
        if (!this.valid(generation) || revision !== this.revision) return
        this.unavailable.clear()
        status.documents = status.documents.map(doc => {
          const existing = this.state.status?.documents.find(item => item.document_id === doc.document_id)?.vector_index
          return existing && existing.operation_id === doc.vector_index?.operation_id
            && (existing.state_version ?? 0) > (doc.vector_index?.state_version ?? 0) ? { ...doc, vector_index: existing } : doc
        })
        this.update({ status, queryError: '' })
        // 查询列表不能凭旧快照解锁未确认 POST；已知任务须单独查询。
        const upload = this.state.upload
        const doc = status.documents.find(doc => doc.document_id === upload?.document_id)
        if (upload && !doc) this.update({ upload: null })
        if (upload && doc?.vector_index && !this.state.uncertain.includes(upload.operation_id || '')) {
          this.update({ upload: { ...upload, vector_index: doc.vector_index } })
        }
      } catch (error) {
        if (this.valid(generation)) this.update({ queryError: formatApiError(error, '刷新状态失败，已保留上次结果。') })
      } finally {
        if (this.valid(generation)) { this.update({ loading: false }); this.planPoll() }
      }
    })()
    this.read = read
    void read.finally(() => { if (this.read === read) this.read = undefined })
    return read
  }

  private apply(receipt: Receipt) {
    const id = receipt.operation_id
    const current = this.state.status?.documents.find(doc => doc.document_id === receipt.document_id)?.vector_index
      ?? this.state.upload?.vector_index
    if (receipt.state_confirmed && receipt.vector_index?.operation_id === current?.operation_id
      && (receipt.vector_index?.state_version ?? 0) < (current?.state_version ?? 0)) return
    const uncertain = new Set(this.state.uncertain)
    if (id) {
      if (receipt.state_confirmed) uncertain.delete(id)
      else uncertain.add(id)
    }
    const uncertainDocuments = { ...this.state.uncertainDocuments }
    if (id && !receipt.state_confirmed) uncertainDocuments[receipt.document_id] = id
    else delete uncertainDocuments[receipt.document_id]
    const documents = this.state.status?.documents.map(doc => doc.document_id === receipt.document_id
      ? { ...doc, vector_index: receipt.vector_index } : doc)
    const upload = 'chunk_count' in receipt ? receipt : this.state.upload?.document_id === receipt.document_id
      ? { ...this.state.upload, vector_index: receipt.vector_index, state_confirmed: receipt.state_confirmed } : this.state.upload
    this.update({ uncertain: [...uncertain], uncertainDocuments, upload,
      status: documents && this.state.status ? { ...this.state.status, documents } : this.state.status })
  }

  query = async (id: string) => {
    const generation = this.generation, revision = this.revision
    try {
      const receipt = await this.api.fetchIndexOperation(id, this.scope, this.signal())
      if (this.valid(generation) && revision === this.revision) {
        this.unavailable.delete(id)
        this.apply(receipt)
        this.update({ queryError: '' })
        if (receipt.state_confirmed && this.errorOperation === id) {
          this.errorOperation = null
          this.update({ error: '' })
        }
      }
    } catch (error) {
      if (this.valid(generation)) {
        if (error instanceof ApiError && (error.status === 404 || error.status === 403)) this.unavailable.add(id)
        this.update({ queryError: formatApiError(error, '暂时无法查询索引状态，已保留上次结果。') })
      }
    } finally {
      if (this.valid(generation)) this.planPoll()
    }
  }

  upload = async (request: IndexDocumentRequest) => {
    if (this.state.uploading || this.state.uploadUncertain || !this.active) return
    const generation = this.generation
    this.revision++
    this.update({ uploading: true, error: '', upload: null })
    try {
      const receipt = await this.api.indexKnowledgeBaseDocument(request, this.abort.signal)
      if (this.valid(generation)) this.apply(receipt)
    } catch (error) {
      if (this.valid(generation)) {
        const receipt = errorReceipt(error)
        if (receipt) this.apply(receipt)
        this.errorOperation = receipt?.operation_id ?? null
        const uncertain = !receipt && (!(error instanceof ApiError) || error.status >= 500)
        this.update({ error: uncertain ? '上传结果尚未确认，请先刷新列表核对，勿重复提交；需重新上传时请核对后重新选择文件。' : formatApiError(error),
          uploadUncertain: uncertain })
      }
    } finally {
      if (this.valid(generation)) {
        this.revision++
        this.update({ uploading: false })
        await this.refresh()
        this.planPoll()
      }
    }
  }

  execute = async (id: string, action: Action) => {
    const index = this.state.status?.documents.find(doc => doc.vector_index?.operation_id === id)?.vector_index
      ?? (this.state.upload?.vector_index?.operation_id === id ? this.state.upload.vector_index : null)
    if (!this.active || this.locks.has(id) || this.state.uncertain.includes(id) || !index
      || index.state_version === null || indexPresentation(index).action !== action) return
    const generation = this.generation
    this.locks.add(id)
    this.errorOperation = null
    this.revision++
    this.update({ busy: [...this.locks], error: '' })
    try {
      const receipt = await (action === 'resume' ? this.api.resumeIndexOperation : this.api.retryIndexOperation)(
        id, index.state_version, this.scope, this.abort.signal,
      )
      if (this.valid(generation)) this.apply(receipt)
    } catch (error) {
      if (this.valid(generation)) {
        const receipt = errorReceipt(error)
        if (receipt) this.apply(receipt)
        this.errorOperation = error instanceof ApiError && error.status === 409 ? null : id
        if (!receipt || !receipt.state_confirmed) this.update({ uncertain: [...new Set([...this.state.uncertain, id])] })
        this.update({ error: error instanceof ApiError && error.status === 409
          ? '任务状态或版本已变化，已重新查询；请核对后再操作。'
          : formatApiError(error, '执行结果暂未确认，将查询任务状态，不会自动重复提交。') })
      }
    } finally {
      if (this.valid(generation)) {
        this.revision++
        this.locks.delete(id)
        this.update({ busy: [...this.locks] })
        // 409/网络异常只 GET 对账，禁止携新版本自动 POST。
        await this.query(id)
        this.planPoll()
      }
    }
  }
}
