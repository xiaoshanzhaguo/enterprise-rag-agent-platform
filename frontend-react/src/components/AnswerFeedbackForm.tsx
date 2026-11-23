import { useEffect, useRef, useState } from 'react'
import { ThumbsDown, ThumbsUp } from 'lucide-react'
import type { AnswerFeedback, FeedbackRating, FeedbackReason } from '../types/chat'
import { feedbackRatingLabels, feedbackReasons, saveAnswerFeedback } from '../api/feedbackApi'
import { ApiError, formatApiError } from '../api/httpClient'
import { SessionActionDialog } from './SessionActionDialog'

export function AnswerFeedbackForm({ sessionId, messageId, initialFeedback }: {
  sessionId: string; messageId: number; initialFeedback?: AnswerFeedback
}) {
  const [saved, setSaved] = useState(initialFeedback)
  const [reason, setReason] = useState<FeedbackReason>('')
  const [comment, setComment] = useState('')
  const [editing, setEditing] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [uncertain, setUncertain] = useState(false)
  const pending = useRef<AbortController | null>(null)
  const negativeButton = useRef<HTMLButtonElement>(null)
  useEffect(() => () => pending.current?.abort(), [])

  function closeFeedback() {
    setEditing(false); setReason(''); setComment(''); setError('')
  }
  async function submit(rating: FeedbackRating) {
    if (saved || uncertain || pending.current || (rating === 'unhelpful' && !reason)) return
    const controller = new AbortController()
    pending.current = controller; setBusy(true); setError('')
    try {
      const result = await saveAnswerFeedback(sessionId, messageId, {
        rating, reason: rating === 'helpful' ? '' : reason, comment: rating === 'helpful' ? '' : comment.trim(),
      },
        AbortSignal.any([controller.signal, AbortSignal.timeout(15000)]))
      if (!controller.signal.aborted) { setSaved(result); setEditing(false) }
    } catch (failure) {
      if (!controller.signal.aborted && (failure instanceof ApiError && failure.status === 409
        || failure instanceof DOMException && failure.name === 'TimeoutError')) setUncertain(true)
      if (!controller.signal.aborted) setError(failure instanceof DOMException && failure.name === 'TimeoutError'
        ? '保存超时，结果尚未确认。请重新进入会话核对；系统不会自动重发。'
        : formatApiError(failure, '反馈保存失败，请稍后重试。'))
    } finally { pending.current = null; if (!controller.signal.aborted) setBusy(false) }
  }
  const locked = busy || Boolean(saved) || uncertain
  const showDialog = editing && !uncertain
  return <div className="answer-feedback" aria-label="回答反馈">
    <div className="feedback-actions"><button type="button" className="feedback-choice" aria-pressed={saved?.rating === 'helpful'} disabled={locked} onClick={() => void submit('helpful')}><ThumbsUp size={13} aria-hidden="true" />{feedbackRatingLabels.helpful}</button>
      <button ref={negativeButton} type="button" className="feedback-choice" aria-haspopup="dialog" aria-pressed={saved?.rating === 'unhelpful'} disabled={locked}
        onClick={() => { setEditing(true); setError('') }}><ThumbsDown size={13} aria-hidden="true" />{feedbackRatingLabels.unhelpful}</button>
      {saved && <span className="feedback-saved" role="status">反馈已记录 · {feedbackRatingLabels[saved.rating]}</span>}</div>
    {showDialog && <SessionActionDialog title="提交回复反馈" busy={busy} onClose={closeFeedback} returnFocusTo={negativeButton}>
      <form className="feedback-form answer-feedback-dialog" onSubmit={event => { event.preventDefault(); void submit('unhelpful') }}>
      <fieldset className="feedback-reasons" disabled={busy}>
        <legend>这条回复有什么问题？</legend>
        {Object.entries(feedbackReasons).map(([value, label]) => <label key={value} className="feedback-reason">
          <input className="sr-only" type="radio" name="feedback-reason" value={value} checked={reason === value}
            onChange={() => setReason(value as FeedbackReason)} /><span>{label}</span>
        </label>)}
      </fieldset>
      <label className="field"><span>补充说明（选填）</span><textarea rows={3} value={comment} maxLength={500} disabled={busy}
        placeholder="请简要说明遇到的问题" onChange={event => setComment(event.target.value)} /></label>
      {error && <div className="alert error" role="alert">{error}</div>}
      <div className="feedback-form-actions">
        <button type="button" className="ghost-button" disabled={busy}
          onClick={closeFeedback}>取消</button>
        <button type="submit" className="primary-button" disabled={busy || !reason}>
          {busy ? '保存中…' : '提交反馈'}
        </button>
      </div>
      </form>
    </SessionActionDialog>}
    {error && !showDialog && <div className="alert error" role="alert">{error}</div>}
  </div>
}
