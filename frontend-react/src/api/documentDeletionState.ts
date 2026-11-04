import { ApiError, formatApiError } from './httpClient'
import type { DocumentDeletionResponse, IndexOperationScope, RagStatusResponse } from '../types/knowledgeBase'

export interface DeletionApi {
  deleteDocument(id: number, version: number, scope: IndexOperationScope, signal?: AbortSignal): Promise<DocumentDeletionResponse>
  fetchDocumentDeletion(id: string, scope: IndexOperationScope, signal?: AbortSignal): Promise<DocumentDeletionResponse>
  retryDocumentDeletion(id: string, version: number, scope: IndexOperationScope, signal?: AbortSignal): Promise<DocumentDeletionResponse>
  fetchRagStatus(session: string, kb?: string, signal?: AbortSignal): Promise<RagStatusResponse>
}

export class DocumentDeletionController {
  private state: { receipt: DocumentDeletionResponse | null; busy: boolean; uncertain: boolean; error: string } = {
    receipt: null, busy: false, uncertain: false, error: '',
  }
  private listeners = new Set<() => void>()
  private active = false
  private generation = 0
  private revision = 0
  private timer: ReturnType<typeof setTimeout> | undefined
  private abort = new AbortController()
  private querying = false
  private completedNotified = false
  private acceptedNotified = false

  private documentId: number
  private version: number
  private scope: IndexOperationScope
  private api: DeletionApi
  private changed: () => void
  private schedule: { set: (fn: () => void) => ReturnType<typeof setTimeout>; clear: (id: ReturnType<typeof setTimeout>) => void }
  constructor(documentId: number, version: number, scope: IndexOperationScope,
    api: DeletionApi, changed: () => void,
    schedule = { set: (fn: () => void) => setTimeout(fn, 3000), clear: (id: ReturnType<typeof setTimeout>) => clearTimeout(id) }) {
    this.documentId = documentId
    this.version = version
    this.scope = scope
    this.api = api
    this.changed = changed
    this.schedule = schedule
  }

  getSnapshot = () => this.state
  subscribe = (listener: () => void) => { this.listeners.add(listener); return () => { this.listeners.delete(listener) } }
  private update(value: Partial<typeof this.state>) { this.state = { ...this.state, ...value }; this.listeners.forEach(fn => fn()) }
  start() { this.active = true; this.abort = new AbortController(); this.plan() }
  dispose() { this.active = false; this.generation++; this.abort.abort(); if (this.timer !== undefined) this.schedule.clear(this.timer) }
  private signal() { return AbortSignal.any([this.abort.signal, AbortSignal.timeout(30000)]) }
  receive = (receipt?: DocumentDeletionResponse | null) => {
    if (!this.active || !receipt || receipt.document_id !== this.documentId) return
    if (this.state.receipt && receipt.state_version < this.state.receipt.state_version) return
    this.update({ receipt, uncertain: !receipt.state_confirmed, error: receipt.error_message || '' })
    if (!this.acceptedNotified) { this.acceptedNotified = true; this.changed() }
    if (receipt.state_confirmed && receipt.status === 'completed' && !this.completedNotified) {
      this.completedNotified = true
      this.changed()
    }
    this.plan()
  }
  private plan() {
    if (this.timer !== undefined) this.schedule.clear(this.timer)
    this.timer = undefined
    if (this.active && !this.state.busy && !this.querying && (this.state.uncertain || this.state.receipt?.status === 'pending')) {
      this.timer = this.schedule.set(() => { this.timer = undefined; void this.query() })
    }
  }
  query = async () => {
    if (!this.active || this.querying) return
    this.querying = true
    const generation = this.generation, revision = this.revision
    try {
      let receipt = this.state.receipt
      if (receipt) receipt = await this.api.fetchDocumentDeletion(receipt.operation_id, this.scope, this.signal())
      else {
        const status = await this.api.fetchRagStatus(this.scope.session_id, this.scope.knowledge_base_id, this.signal())
        receipt = status.documents.find(doc => doc.document_id === this.documentId)?.deletion ?? null
      }
      if (this.active && generation === this.generation && revision === this.revision && receipt) this.receive(receipt)
    } catch (error) {
      if (this.active && generation === this.generation) this.update({ error: formatApiError(error, '查询失败，已保留删除回执。') })
    } finally { this.querying = false; this.plan() }
  }
  remove = async () => this.mutate(false)
  retry = async () => this.mutate(true)
  private async mutate(retry: boolean) {
    if (!this.active || this.state.busy || this.state.uncertain) return
    const task = this.state.receipt
    if (retry ? !task?.retryable || task.execution_claimed || !['failed', 'pending'].includes(task.status) : Boolean(task)) return
    this.revision++
    const generation = this.generation
    this.update({ busy: true, error: '' })
    try {
      const receipt = retry && task
        ? await this.api.retryDocumentDeletion(task.operation_id, task.state_version, this.scope, this.signal())
        : await this.api.deleteDocument(this.documentId, this.version, this.scope, this.signal())
      if (this.active && generation === this.generation) this.receive(receipt)
    } catch (error) {
      if (this.active && generation === this.generation) {
        const payload = error instanceof ApiError ? error.payload : null
        if (payload && typeof payload === 'object' && 'operation_id' in payload && 'state_confirmed' in payload) {
          this.receive(payload as DocumentDeletionResponse)
        } else this.update({ uncertain: !(error instanceof ApiError) || error.status >= 500,
          error: formatApiError(error, '删除结果尚未确认，请查询核对，系统不会自动重新删除。') })
        this.changed()
      }
    } finally {
      if (this.active && generation === this.generation) {
        this.revision++
        this.update({ busy: false })
        if (this.state.uncertain || this.state.receipt?.status === 'pending') void this.query()
        this.plan()
      }
    }
  }
}
