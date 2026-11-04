import { apiDelete, apiGet, apiPost } from './httpClient'
import type { DocumentDeletionResponse, IndexOperationScope } from '../types/knowledgeBase'

function scopeQuery(scope: IndexOperationScope) {
  return new URLSearchParams({ knowledge_base_id: scope.knowledge_base_id || '', user_role: scope.user_role || '' })
}
export function deleteDocument(id: number, version: number, scope: IndexOperationScope, signal?: AbortSignal) {
  return apiDelete<DocumentDeletionResponse>(`/documents/${id}?${scopeQuery(scope)}`,
    { expected_document_version: version }, signal)
}
export function fetchDocumentDeletion(id: string, scope: IndexOperationScope, signal?: AbortSignal) {
  return apiGet<DocumentDeletionResponse>(`/document_deletions/${encodeURIComponent(id)}?${scopeQuery(scope)}`, signal)
}
export function retryDocumentDeletion(id: string, version: number, scope: IndexOperationScope, signal?: AbortSignal) {
  return apiPost<{ expected_state_version: number }, DocumentDeletionResponse>(
    `/document_deletions/${encodeURIComponent(id)}/retry?${scopeQuery(scope)}`, { expected_state_version: version }, signal)
}
