import { indexPresentation } from '../api/knowledgeIndexState'
import type { VectorIndexStatus } from '../types/knowledgeBase'

interface Props {
  index?: VectorIndexStatus | null
  operationId?: string | null
  uncertain?: boolean
  busy?: boolean
  onQuery?: () => void
  onExecute?: (action: 'resume' | 'retry') => void
}

export function DocumentIndexStatus({ index, operationId, uncertain = false, busy = false, onQuery, onExecute }: Props) {
  const view = indexPresentation(index, uncertain)
  const hasOperation = Boolean(index?.operation_id || operationId)
  const canQuery = hasOperation && onQuery && (uncertain || index?.status === 'pending' || index?.status === 'failed')
  const canExecute = hasOperation && view.action && onExecute
  return (
    <div className="index-status" aria-live="polite">
      <div className="index-status-heading">
        <span className={`status-badge ${view.tone}`}><i aria-hidden="true" />{view.label}</span>
        {canQuery || canExecute ? <div className="index-status-actions">
          {canQuery ? <button type="button" className="ghost-button" disabled={busy} onClick={onQuery}>查询状态</button> : null}
          {canExecute ? <button type="button" className="ghost-button index-action" disabled={busy}
            onClick={() => { if (view.action) onExecute(view.action) }}>
            {busy ? '处理中…' : view.action === 'resume' ? '继续执行' : '重试索引'}
          </button> : null}
        </div> : null}
      </div>
      {view.detail ? <p className="index-status-detail">{view.detail}</p> : null}
    </div>
  )
}
