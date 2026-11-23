import { useEffect, useRef, useState } from 'react'
import type { RefObject } from 'react'
import { ClipboardCheck } from 'lucide-react'
import { feedbackRatingLabels, feedbackReasons, reviewStatuses, saveFeedbackReview } from '../api/feedbackApi'
import type { FeedbackRecord, ReviewStatus } from '../api/feedbackApi'
import { ApiError, formatApiError } from '../api/httpClient'
import { ScopeSelect } from './ScopeSelect'
import { answerBody } from '../api/sessionMessages'
import { SessionActionDialog } from './SessionActionDialog'

export function FeedbackReviewEditor({ item, onSaved, onClose, returnFocusTo }: {
  item: FeedbackRecord; onSaved: () => void; onClose: () => void; returnFocusTo: RefObject<HTMLButtonElement | null>
}) {
  const [status, setStatus] = useState(item.review_status)
  const [note, setNote] = useState(item.review_note)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [conflict, setConflict] = useState(false)
  const pending = useRef<AbortController | null>(null)
  useEffect(() => () => pending.current?.abort(), [])

  async function save() {
    if (item.rating !== 'unhelpful' || pending.current || conflict || (status === 'resolved' && !note.trim())) return
    const controller = new AbortController()
    pending.current = controller; setBusy(true); setError('')
    try {
      await saveFeedbackReview(item.message_id, status, note.trim(), item.revision,
        AbortSignal.any([controller.signal, AbortSignal.timeout(15000)]))
      if (!controller.signal.aborted) onSaved()
    } catch (failure) {
      if (!controller.signal.aborted) {
        setConflict(failure instanceof ApiError && failure.status === 409)
        setError(failure instanceof DOMException && failure.name === 'TimeoutError'
          ? '保存超时，结果尚未确认，请刷新反馈核对。系统不会自动重发。'
          : formatApiError(failure, '处理结果保存失败，请稍后重试。'))
      }
    } finally { pending.current = null; if (!controller.signal.aborted) setBusy(false) }
  }

  return <SessionActionDialog title="反馈详情" busy={busy} onClose={onClose} returnFocusTo={returnFocusTo}>
    <div className="feedback-detail">
      <div className="feedback-detail-meta">
        <span className={`review-badge feedback-rating-${item.rating}`}>{feedbackRatingLabels[item.rating]}</span>
        {item.reason && <span>{feedbackReasons[item.reason]}</span>}
        <small>回答 #{item.message_id}</small>
      </div>
      <dl className="feedback-detail-content">
        <div><dt>问题</dt><dd>{item.question || '历史问题未记录'}</dd></div>
        <div><dt>回答</dt><dd>{answerBody(item.answer)}</dd></div>
        {item.comment && <div><dt>用户建议</dt><dd>{item.comment}</dd></div>}
        {item.rating === 'helpful' && item.review_note && <div><dt>历史处理说明</dt><dd>{item.review_note}</dd></div>}
      </dl>
      {item.rating === 'unhelpful' ? <form className="feedback-form feedback-detail-form" onSubmit={event => { event.preventDefault(); void save() }}>
      <ScopeSelect label="处理状态" icon={<ClipboardCheck size={17} aria-hidden="true" />} value={status} disabled={busy || conflict}
        options={Object.entries(reviewStatuses).map(([value, label]) => ({ value, label }))}
        onChange={value => setStatus(value as ReviewStatus)} />
      <label className="field"><span>处理说明与复核结果（最多 1000 字）</span><textarea value={note} maxLength={1000} rows={3} required={status === 'resolved'} disabled={busy || conflict}
        placeholder="记录补充了哪些资料、调整了什么，以及原问题复测结果。" onChange={event => setNote(event.target.value)} /></label>
      <p className="page-footnote">标记已解决时需填写处理说明。</p>
      {error && <div className="alert error" role="alert">{error}</div>}
      <div className="session-dialog-actions">
        <button className="ghost-button" type="button" disabled={busy} onClick={onClose}>取消</button>
        <button className="primary-button" type="submit" disabled={busy || conflict || (status === 'resolved' && !note.trim())}>{busy ? '保存中…' : '保存处理结果'}</button>
      </div>
      </form> : <div className="session-dialog-actions">
        <button className="ghost-button" type="button" onClick={onClose}>关闭</button>
      </div>}
    </div>
  </SessionActionDialog>
}
