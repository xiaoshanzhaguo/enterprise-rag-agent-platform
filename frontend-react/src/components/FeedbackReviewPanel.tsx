import { useEffect, useRef, useState } from 'react'
import { ClipboardCheck, MessageSquare, RefreshCw } from 'lucide-react'
import { feedbackRatingLabels, feedbackReasons, fetchFeedback, reviewStatuses } from '../api/feedbackApi'
import type { FeedbackList, FeedbackRecord } from '../api/feedbackApi'
import { formatApiError } from '../api/httpClient'
import { EmptyState, ErrorNotice } from './WorkspaceUI'
import { FeedbackReviewEditor } from './FeedbackReviewEditor'
import { ScopeSelect } from './ScopeSelect'
import { DEFAULT_TABLE_PAGE_SIZE, TablePagination } from './TablePagination'

export function FeedbackReviewPanel() {
  const [data, setData] = useState<FeedbackList | null>(null)
  const [rating, setRating] = useState('')
  const [reviewStatus, setReviewStatus] = useState('')
  const [notice, setNotice] = useState('')
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(DEFAULT_TABLE_PAGE_SIZE)
  const [revision, setRevision] = useState(0)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [selected, setSelected] = useState<FeedbackRecord | null>(null)
  const detailTrigger = useRef<HTMLButtonElement>(null)
  useEffect(() => {
    const controller = new AbortController()
    async function load() {
      setLoading(true); setError('')
      try {
        const result = await fetchFeedback(rating, page, AbortSignal.any([controller.signal, AbortSignal.timeout(15000)]), reviewStatus, pageSize)
        if (!controller.signal.aborted) {
          const lastPage = Math.max(1, Math.ceil(result.total / result.page_size))
          if (page > lastPage) setPage(lastPage)
          else setData(result)
        }
      } catch (failure) { if (!controller.signal.aborted) setError(formatApiError(failure, '反馈列表暂不可用。')) }
      finally { if (!controller.signal.aborted) setLoading(false) }
    }
    void load()
    return () => controller.abort()
  }, [rating, page, pageSize, revision, reviewStatus])
  const refresh = () => setRevision(value => value + 1)
  return <section className="page-stack">
    <header className="page-header"><h2>问答反馈</h2></header>
    <section className="panel feedback-review" aria-label="用户回答反馈">
    <div className="log-toolbar">
      <ScopeSelect label="反馈筛选" hideLabel icon={<MessageSquare size={17} aria-hidden="true" />} value={rating}
        options={[{ value: '', label: '全部反馈' }, ...Object.entries(feedbackRatingLabels).map(([value, label]) => ({ value, label }))]}
        onChange={value => { setRating(value); if (value === 'helpful') setReviewStatus(''); setPage(1); setNotice('') }} />
      <ScopeSelect label="处理状态筛选" hideLabel icon={<ClipboardCheck size={17} aria-hidden="true" />} value={reviewStatus} disabled={rating === 'helpful'}
        options={[{ value: '', label: '全部处理状态' }, ...Object.entries(reviewStatuses).map(([value, label]) => ({ value, label }))]}
        onChange={value => { setReviewStatus(value); setPage(1); setNotice('') }} />
      <button className="ghost-button" type="button" disabled={loading} onClick={refresh}><RefreshCw size={14} aria-hidden="true" />刷新反馈</button></div>
    {notice && <p className="feedback-saved" role="status">{notice}</p>}
    {loading ? <p role="status">正在读取反馈…</p> : error ? <ErrorNotice message={error} retry={refresh} /> : data && <>
      <div className="review-counts" aria-label="全部反馈统计">
        <span>{feedbackRatingLabels.helpful} <strong>{data.counts.helpful}</strong></span><span>{feedbackRatingLabels.unhelpful} <strong>{data.counts.unhelpful}</strong></span>
        {Object.entries(reviewStatuses).map(([status, label]) => <span key={status}>{label} <strong>{data.review_counts[status as keyof typeof reviewStatuses]}</strong></span>)}
      </div>
      {!data.items.length ? <EmptyState title="暂无匹配反馈">在智能问答的完整回答下提交反馈后，可在这里查看。</EmptyState>
        : <div className="admin-table-scroll" tabIndex={0} role="region" aria-label="反馈列表">
          <table className="admin-table feedback-table">
            <caption className="sr-only">问答反馈记录</caption>
            <thead><tr><th scope="col">问题与建议</th><th scope="col">反馈类型</th><th scope="col">处理状态</th><th scope="col">更新时间</th><th scope="col">操作</th></tr></thead>
            <tbody>{data.items.map(item => <tr key={item.message_id}>
              <th scope="row">
                <span className="feedback-table-question">{item.question || '历史问题未记录'}</span>
                {item.comment && <small className="feedback-table-comment" title={item.comment}>{item.comment}</small>}
              </th>
              <td>
                <span className={`review-badge feedback-rating-${item.rating}`}>{feedbackRatingLabels[item.rating]}</span>
                {item.reason && <small>{feedbackReasons[item.reason]}</small>}
              </td>
              <td>{item.rating === 'unhelpful'
                ? <span className={`review-badge review-${item.review_status}`}>{reviewStatuses[item.review_status]}</span>
                : <span aria-label="仅供查看">—</span>}</td>
              <td><time>{item.updated_at.replace('T', ' ')}</time></td>
              <td><button type="button" className="text-button" onClick={event => {
                detailTrigger.current = event.currentTarget
                setSelected(item)
                setNotice('')
              }}>{item.rating === 'helpful' ? '查看' : '查看与管理'}<span className="sr-only">回答 #{item.message_id}</span></button></td>
            </tr>)}</tbody>
          </table>
        </div>}
      {data.total > 0 && <TablePagination label="反馈分页" total={data.total} page={data.page} pageSize={data.page_size}
        onPageChange={setPage} onPageSizeChange={size => { setPageSize(size); setPage(1) }} />}
    </>}
    </section>
    {selected && <FeedbackReviewEditor item={selected} returnFocusTo={detailTrigger} onClose={() => setSelected(null)}
      onSaved={() => { setSelected(null); setNotice('处理结果已记录'); refresh() }} />}
  </section>
}
