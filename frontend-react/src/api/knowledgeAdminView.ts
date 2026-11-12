import { groupDocumentVersions } from './documentVersionState'
import type { RagStatusDocument } from '../types/knowledgeBase'

export const MAX_DOCUMENT_BYTES = 2 * 1024 * 1024

export function documentFileError(file: Pick<File, 'name' | 'size'>) {
  if (!/\.(md|txt)$/i.test(file.name)) return '请选择 Markdown（.md）或纯文本（.txt）文件。'
  if (file.size === 0) return '文件内容为空，请选择包含正文的文档。'
  if (file.size > MAX_DOCUMENT_BYTES) return '文件超过 2 MB，请缩小文件后重新选择。'
  return ''
}

export function knowledgeAdminView(documents: RagStatusDocument[], query: string) {
  const groups = groupDocumentVersions(documents)
  const keyword = query.trim().toLocaleLowerCase()
  return {
    groups,
    filteredGroups: groups.filter(group => !keyword || group.name.toLocaleLowerCase().includes(keyword)
      || group.versions.some(version => version.file_name?.toLocaleLowerCase().includes(keyword))),
    currentCount: documents.filter(document => document.is_current_version && document.logical_enabled !== 0
      && document.lifecycle_state !== 'deleting').length,
    versionCount: documents.length,
  }
}
