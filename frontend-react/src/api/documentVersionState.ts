import type { RagStatusDocument, KnowledgeBaseCategory } from '../types/knowledgeBase'

export function versionConfirmation(document: RagStatusDocument, action: 'publish' | 'disable') {
  return { action, stateVersion: document.logical_state_version, documentId: document.document_id,
    identity: document.logical_document_id }
}

export function versionUploadMetadata(documents: RagStatusDocument[], identity: string, category: KnowledgeBaseCategory) {
  const versions = documents.filter(d => d.logical_document_id === identity)
  const previous = versions.find(d => d.is_current_version) || versions.sort((a, b) => (b.version_number || 1) - (a.version_number || 1))[0]
  return { knowledge_base_type: category.knowledge_base_type,
    department: identity && previous?.knowledge_base_type === category.knowledge_base_type ? undefined : category.department,
    process_status: identity ? undefined : 'active',
  }
}

export function groupDocumentVersions(documents: RagStatusDocument[]) {
  const groups = new Map<string, RagStatusDocument[]>()
  for (const document of documents) {
    const key = document.logical_document_id || `document:${document.document_id}`
    groups.set(key, [...(groups.get(key) || []), document])
  }
  return [...groups].map(([id, versions]) => ({ id,
    name: versions[0].display_name || versions[0].file_name || '未命名文档',
    versions: versions.sort((a, b) => (b.version_number || 1) - (a.version_number || 1)),
  }))
}

export function versionPresentation(document: RagStatusDocument) {
  const status = document.vector_index?.status || 'unknown'
  return {
    label: document.is_current_version ? '当前生效' : document.logical_enabled === 0 ? '已停用' : document.published_at ? '历史版本' : '候选版本',
    publishable: document.lifecycle_state !== 'deleting' && !document.is_current_version &&
      (['ready', 'not_requested'].includes(status) || (status === 'unknown' && Boolean(document.published_at))),
    deletable: !document.is_current_version && !document.vector_index?.execution_claimed && document.lifecycle_state !== 'deleting',
  }
}
