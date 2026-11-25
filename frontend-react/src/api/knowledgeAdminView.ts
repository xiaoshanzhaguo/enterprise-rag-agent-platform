import { groupDocumentVersions } from './documentVersionState'
import type { KnowledgeBaseItem, RagStatusDocument } from '../types/knowledgeBase'

export const MAX_DOCUMENT_BYTES = 2 * 1024 * 1024
export const DOCUMENT_ACCEPT = '.md,.txt,.docx,.pdf,.png,.jpg,.jpeg,.webp'
const DOCUMENT_EXTENSIONS = new Set(DOCUMENT_ACCEPT.split(','))

export function isPlainTextDocument(fileName: string) {
  return /\.(md|txt)$/i.test(fileName)
}

export function documentFileError(file: Pick<File, 'name' | 'size'>) {
  const suffix = /\.[^.]+$/.exec(file.name)?.[0].toLowerCase() ?? ''
  if (!DOCUMENT_EXTENSIONS.has(suffix)) return '请选择 Markdown、TXT、Word（.docx）、PDF 或 PNG / JPG / WEBP 图片。'
  if (file.size === 0) return '文件内容为空，请选择包含正文的文档。'
  if (file.size > MAX_DOCUMENT_BYTES) return '文件超过 2 MB，请缩小文件后重新选择。'
  return ''
}

export function searchKnowledgeBases(knowledgeBases: KnowledgeBaseItem[], query: string) {
  const keyword = query.trim().toLocaleLowerCase()
  return knowledgeBases.filter(item => item.name.toLocaleLowerCase().includes(keyword))
}

export function knowledgeAdminView(documents: RagStatusDocument[]) {
  return {
    groups: groupDocumentVersions(documents),
    currentCount: documents.filter(document => document.is_current_version && document.logical_enabled !== 0
      && document.lifecycle_state !== 'deleting').length,
    versionCount: documents.length,
  }
}
