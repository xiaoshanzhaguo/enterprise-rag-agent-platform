import { useEffect, useRef, useState } from 'react'
import { ThumbsDown, ThumbsUp } from 'lucide-react'
import type { AnswerFeedback, FeedbackRating, FeedbackReason } from '../types/chat'
import { feedbackReasons, saveAnswerFeedback } from '../api/feedbackApi'
import { formatApiError } from '../api/httpClient'

export function AnswerFeedbackForm({ sessionId, messageId, initialFeedback }: {
  sessionId: string; messageId: number; initialFeedback?: AnswerFeedback
}) {
  const [saved, setSaved] = useState(initialFeedback)
  const [rating, setRating] = useState<FeedbackRating | undefined>(initialFeedback?.rating)
  const [reason, setReason] = useState<FeedbackReason>(initialFeedback?.reason || '')
  const [comment, setComment] = useState(initialFeedback?.comment || '')
  const [editing, setEditing] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const pending = useRef<AbortController | null>(null)
  useEffect(() => () => pending.current?.abort(), [])

  function choose(value: FeedbackRating) {
    setRating(value); setReason(value === 'helpful' ? '' : saved?.reason || '')
    setEditing(true); setError('')
  }
  async function submit() {
    if (pending.current || !rating || (rating === 'unhelpful' && !reason)) return
    const controller = new AbortController()
    pending.current = controller; setBusy(true); setError('')
    try {
      const result = await saveAnswerFeedback(sessionId, messageId, { rating, reason: rating === 'helpful' ? '' : reason, comment: comment.trim() },
        AbortSignal.any([controller.signal, AbortSignal.timeout(15000)]))
      if (!controller.signal.aborted) { setSaved(result); setEditing(false) }
    } catch (failure) {
      if (!controller.signal.aborted) setError(failure instanceof DOMException && failure.name === 'TimeoutError'
        ? '保存超时，结果尚未确认。请刷新历史核对；系统不会自动重发。'
        : formatApiError(failure, '反馈保存失败，请稍后重试。'))
    } finally { pending.current = null; if (!controller.signal.aborted) setBusy(false) }
  }
  return <div className="answer-feedback" aria-label="回答反馈">
    <div className="feedback-actions"><button type="button" className="feedback-choice" aria-pressed={rating === 'helpful'} disabled={busy} onClick={() => choose('helpful')}><ThumbsUp size={13} />有帮助</button>
      <button type="button" className="feedback-choice" aria-pressed={rating === 'unhelpful'} disabled={busy} onClick={() => choose('unhelpful')}><ThumbsDown size={13} />无帮助</button>
      {saved && !editing && <span className="feedback-saved" role="status">反馈已记录 · {saved.rating === 'helpful' ? '有帮助' : '无帮助'}</span>}</div>
    {editing && <form className="feedback-form" onSubmit={event => { event.preventDefault(); void submit() }}>
      {saved && <p className="page-footnote">修改反馈内容会重置关联处理状态和处理说明。</p>}
      {rating === 'unhelpful' && <label className="field"><span>问题原因</span><select value={reason} disabled={busy} required onChange={event => setReason(event.target.value as FeedbackReason)}>
        <option value="">请选择原因</option>{Object.entries(feedbackReasons).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>}
      <label className="field"><span>补充说明（选填，最多 500 字）</span><textarea rows={2} value={comment} maxLength={500} disabled={busy} placeholder="例如：缺少审批时限或引用的制度不相关" onChange={event => setComment(event.target.value)} /></label>
      <div className="feedback-actions"><button type="submit" className="ghost-button" disabled={busy || !rating || (rating === 'unhelpful' && !reason)}>{busy ? '保存中…' : '提交反馈'}</button>
        <button type="button" className="text-button" disabled={busy} onClick={() => { setEditing(false); setRating(saved?.rating); setReason(saved?.reason || ''); setComment(saved?.comment || ''); setError('') }}>取消</button></div>
    </form>}
    {error && <div className="alert error" role="alert">{error}</div>}
  </div>
}
