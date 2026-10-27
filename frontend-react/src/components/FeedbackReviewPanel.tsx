import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { MessageSquare, RefreshCw } from 'lucide-react'
import { feedbackReasons, fetchFeedback, reviewStatuses } from '../api/feedbackApi'
import type { FeedbackList } from '../api/feedbackApi'
import { answerBody } from '../api/sessionMessages'
import { formatApiError } from '../api/httpClient'
import { EmptyState, ErrorNotice } from './WorkspaceUI'
import { FeedbackReviewEditor } from './FeedbackReviewEditor'

export function FeedbackReviewPanel() {
  const [data, setData] = useState<FeedbackList | null>(null)
  const [rating, setRating] = useState('')
  const [reviewStatus, setReviewStatus] = useState('')
  const [notice, setNotice] = useState('')
  const [page, setPage] = useState(1)
  const [revision, setRevision] = useState(0)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  useEffect(() => {
    const controller = new AbortController()
    async function load() {
      setLoading(true); setError('')
      try {
        const result = await fetchFeedback(rating, page, AbortSignal.any([controller.signal, AbortSignal.timeout(15000)]), reviewStatus)
        if (!controller.signal.aborted) setData(result)
      } catch (failure) { if (!controller.signal.aborted) setError(formatApiError(failure, '反馈列表暂不可用。')) }
      finally { if (!controller.signal.aborted) setLoading(false) }
    }
    void load()
    return () => controller.abort()
  }, [rating, page, revision, reviewStatus])
  const refresh = () => setRevision(value => value + 1)
  return <section className="panel feedback-review" aria-label="用户回答反馈">
    <div className="panel-heading"><MessageSquare size={20} /><div><h3>用户回答反馈</h3><p>来自本机已保存回答的最新反馈，可回到原会话核对依据。</p></div></div>
    <div className="log-toolbar"><label className="filter-field"><span className="sr-only">反馈筛选</span><select value={rating} onChange={event => { setRating(event.target.value); setReviewStatus(''); setPage(1); setNotice('') }}>
      <option value="">全部反馈</option><option value="helpful">有帮助</option><option value="unhelpful">无帮助</option></select></label>
      <label className="filter-field"><span className="sr-only">处理状态筛选</span><select value={reviewStatus} onChange={event => { setReviewStatus(event.target.value); if (event.target.value) setRating('unhelpful'); setPage(1); setNotice('') }}>
        <option value="">全部处理状态</option>{Object.entries(reviewStatuses).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
      <button className="ghost-button" disabled={loading} onClick={refresh}><RefreshCw size={14} />刷新反馈</button></div>
    {notice && <p className="feedback-saved" role="status">{notice}</p>}
    {loading ? <p role="status">正在读取反馈…</p> : error ? <ErrorNotice message={error} retry={refresh} /> : data && <>
      <p className="page-footnote">全部已记录反馈：有帮助 {data.counts.helpful} 条 · 无帮助 {data.counts.unhelpful} 条。每条回答保留最新反馈，不作为多人满意率。</p>
      <div className="review-counts">{Object.entries(reviewStatuses).map(([status, label]) => <span key={status}>{label} <strong>{data.review_counts[status as keyof typeof reviewStatuses]}</strong></span>)}</div>
      {!data.items.length ? <EmptyState title="暂无匹配反馈">在智能问答的完整回答下提交反馈后，可在这里查看。</EmptyState> : <div className="feedback-list">{data.items.map(item => <article className="feedback-record" key={item.message_id}>
        <div className="feedback-record-heading"><strong>{item.rating === 'helpful' ? '有帮助' : '无帮助'}{item.reason && ` · ${feedbackReasons[item.reason]}`}</strong>
          <Link to={`/chat?session=${encodeURIComponent(item.session_id)}`}>查看原会话</Link></div>
        <h4>{item.question || '历史问题未记录'}</h4><p className="feedback-answer">{answerBody(item.answer)}</p>
        {item.comment && <p className="feedback-comment">反馈说明：{item.comment}</p>}<small>回答 #{item.message_id} · {item.updated_at}</small>
        {item.rating === 'unhelpful' && <FeedbackReviewEditor key={`${item.message_id}-${item.revision}`} item={item} onEdit={() => setNotice('')} onSaved={() => { setNotice('处理结果已记录'); setPage(1); refresh() }} />}
      </article>)}</div>}
      <div className="pagination"><span>共 {data.total} 条 · 第 {page} / {Math.max(1, Math.ceil(data.total/10))} 页</span><div>
        <button className="ghost-button" disabled={page <= 1} onClick={() => setPage(value => value-1)}>上一页</button><button className="ghost-button" disabled={page*10 >= data.total} onClick={() => setPage(value => value+1)}>下一页</button></div></div>
    </>}
  </section>
}
