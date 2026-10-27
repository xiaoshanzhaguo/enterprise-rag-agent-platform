import { useEffect, useRef, useState } from 'react'
import { reviewStatuses, saveFeedbackReview } from '../api/feedbackApi'
import type { FeedbackRecord, ReviewStatus } from '../api/feedbackApi'
import { ApiError, formatApiError } from '../api/httpClient'

export function FeedbackReviewEditor({ item, onSaved, onEdit }: { item: FeedbackRecord; onSaved: () => void; onEdit: () => void }) {
  const [editing, setEditing] = useState(false)
  const [status, setStatus] = useState(item.review_status)
  const [note, setNote] = useState(item.review_note)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [conflict, setConflict] = useState(false)
  const pending = useRef<AbortController | null>(null)
  useEffect(() => () => pending.current?.abort(), [])

  async function save() {
    if (pending.current || conflict || (status === 'resolved' && !note.trim())) return
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

  return <div className="feedback-resolution" aria-label={`处理回答 ${item.message_id} 的反馈`}>
    <div className="feedback-actions"><span className={`review-badge review-${item.review_status}`}>{reviewStatuses[item.review_status]}</span>
      {!editing && <button className="text-button" type="button" onClick={() => { onEdit(); setEditing(true) }}>处理反馈</button>}</div>
    {item.review_note && !editing && <p className="feedback-comment">处理说明：{item.review_note}</p>}
    {editing && <form className="feedback-form" onSubmit={event => { event.preventDefault(); void save() }}>
      <label className="field"><span>处理状态</span><select disabled={busy || conflict} value={status} onChange={event => setStatus(event.target.value as ReviewStatus)}>
        {Object.entries(reviewStatuses).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
      <label className="field"><span>处理说明与复核结果（最多 1000 字）</span><textarea value={note} maxLength={1000} rows={3} required={status === 'resolved'} disabled={busy || conflict}
        placeholder="记录补充了哪些资料、调整了什么，以及原问题复测结果。" onChange={event => setNote(event.target.value)} /></label>
      <p className="page-footnote">标记已解决需填写说明；请先回到原会话复核问题和引用。状态由人工记录。</p>
      <div className="feedback-actions"><button className="ghost-button" type="submit" disabled={busy || conflict || (status === 'resolved' && !note.trim())}>{busy ? '保存中…' : '保存处理结果'}</button>
        <button className="text-button" type="button" disabled={busy} onClick={() => { setEditing(false); setStatus(item.review_status); setNote(item.review_note); setError(''); setConflict(false) }}>取消</button></div>
    </form>}
    {error && <div className="alert error" role="alert">{error}</div>}
  </div>
}
