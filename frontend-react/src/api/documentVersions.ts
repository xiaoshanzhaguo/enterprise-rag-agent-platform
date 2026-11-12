import { apiPost } from './httpClient'

export function renameDocument(identity: string, knowledgeBaseId: string, stateVersion: number, displayName: string) {
  const query = new URLSearchParams({ knowledge_base_id: knowledgeBaseId, user_role: 'kb_admin' })
  return apiPost(`/logical_documents/${encodeURIComponent(identity)}/rename?${query}`,
    { expected_state_version: stateVersion, display_name: displayName }, AbortSignal.timeout(15000))
}
export function changeDocumentVersion(identity: string, knowledgeBaseId: string, stateVersion: number,
  documentId?: number) {
  const query = new URLSearchParams({ knowledge_base_id: knowledgeBaseId, user_role: 'kb_admin' })
  return apiPost(`/logical_documents/${encodeURIComponent(identity)}/${documentId ? 'publish' : 'disable'}?${query}`,
    { expected_state_version: stateVersion, ...(documentId ? { document_id: documentId } : {}) }, AbortSignal.timeout(15000))
}
