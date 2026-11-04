import { useRef, useState } from 'react'
import { changeDocumentVersion } from '../api/documentVersions'
import { versionPresentation, versionConfirmation } from '../api/documentVersionState'
import { formatApiError } from '../api/httpClient'
import { SessionActionDialog } from './SessionActionDialog'
import type { RagStatusDocument } from '../types/knowledgeBase'

export function DocumentVersionControls({ document, knowledgeBaseId, onChanged }: {
  document: RagStatusDocument; knowledgeBaseId: string; onChanged: () => void
}) {
  const [confirmation, setConfirmation] = useState<ReturnType<typeof versionConfirmation> | null>(null)
  const action = confirmation?.action
  const [busy, setBusy] = useState(false)
  const lock = useRef(false)
  const [error, setError] = useState('')
  const trigger = useRef<HTMLButtonElement>(null)
  const presentation = versionPresentation(document)
  if (!document.logical_document_id || !document.logical_state_version) return null
  async function confirm() {
    if (lock.current || !confirmation?.stateVersion || !confirmation.identity) return
    lock.current = true; setBusy(true); setError('')
    try {
      await changeDocumentVersion(confirmation.identity, knowledgeBaseId, confirmation.stateVersion,
        action === 'publish' ? confirmation.documentId : undefined)
      setConfirmation(null)
    } catch (error) { setError(formatApiError(error, '版本操作未确认，请刷新后核对。')) }
    finally { lock.current = false; setBusy(false); onChanged() }
  }
  return <div className="document-deletion-controls">
    <span className={`status-badge ${document.is_current_version ? 'status-success' : ''}`}>
      V{document.version_number} · {presentation.label}
    </span>
    {document.is_current_version ? <button ref={trigger} type="button" className="ghost-button" disabled={busy}
      onClick={() => setConfirmation(versionConfirmation(document, 'disable'))}>停用逻辑文档</button> : <button ref={trigger} type="button" className="ghost-button"
      disabled={!presentation.publishable || busy} onClick={() => setConfirmation(versionConfirmation(document, 'publish'))}>{document.published_at ? '回退至此版本' : '发布此版本'}</button>}
    {error ? <p role="alert" className="index-status-detail">{error}</p> : null}
    {action ? <SessionActionDialog title={action === 'publish' ? '确认切换生效版本' : '确认停用逻辑文档'}
      busy={busy} returnFocusTo={trigger} onClose={() => setConfirmation(null)}>
      <p>{action === 'publish' ? `将 V${document.version_number} 设为当前版本，其他版本退出检索。` : '停用后，所有版本立即退出正常检索，历史记录保留。'}</p>
      <div className="session-dialog-actions"><button className="ghost-button" disabled={busy} onClick={() => setConfirmation(null)}>取消</button>
        <button className="primary-button" disabled={busy} onClick={() => { void confirm() }}>{busy ? '提交中…' : '确认'}</button></div>
    </SessionActionDialog> : null}
  </div>
}
