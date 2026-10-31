import { ArrowUpRight, ChevronLeft, ChevronRight, FileClock, RefreshCw, Search, X } from 'lucide-react'
import { useEffect, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { fetchApplicationExecution, fetchApplicationExecutions } from '../api/processApi'
import { formatApiError } from '../api/httpClient'
import { EmptyState, ErrorNotice, StatusBadge } from '../components/WorkspaceUI'
import { formatTime, statusLabels } from '../api/processDisplay'
import type { ProcessExecution, ProcessExecutionPage, ProcessStatus } from '../types/process'

const fieldLabels = { application_type: '申请类型', applicant: '申请人', department: '所属部门', permission_level: '权限级别', approver: '审批人', additional_info: '补充信息' }
const statusHelp: Record<ProcessStatus, string> = {
  simulated: '已收到模拟流程回执并保存记录。此状态不表示真实审批通过或权限已授予。',
  running: '记录已创建，尚无最终回执。请按申请编号核对执行情况，勿重复提交。',
  timeout: '请求超时，对方可能已执行。请先核对 n8n，勿直接重复提交。',
  failed: '未取得有效成功回执。请核对错误与执行记录后再处理。',
}

export function ExecutionLogPage() {
  const [params, setParams] = useSearchParams()
  const requestedPage = Number(params.get('page'))
  const page = Number.isInteger(requestedPage) && requestedPage > 0 && requestedPage <= 100000 ? requestedPage : 1
  const status = params.get('status') || ''
  const requestId = params.get('request_id') || ''
  const [data, setData] = useState<ProcessExecutionPage | null>(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)
  const [revision, setRevision] = useState(0)
  const [selected, setSelected] = useState<string | null>(null)
  const [detail, setDetail] = useState<ProcessExecution | null>(null)
  const [detailError, setDetailError] = useState('')
  const [detailLoading, setDetailLoading] = useState(false)
  const [updated, setUpdated] = useState<string | null>(null)

  useEffect(() => {
    const controller = new AbortController()
    const timer = window.setTimeout(() => controller.abort(), 15000)
    let ignore = false
    async function load() {
      setLoading(true); setError('')
      try {
        const query = new URLSearchParams({ page: String(page), page_size: '10' })
        if (status) query.set('status', status)
        if (requestId) query.set('request_id', requestId)
        const result = await fetchApplicationExecutions(query, controller.signal)
        if (!ignore) { setData(result); setUpdated(new Date().toISOString()) }
      } catch (err) { if (!ignore) setError(controller.signal.aborted ? '查询超时，请稍后刷新。此操作不会重复提交申请。' : formatApiError(err)) }
      finally { if (!ignore) setLoading(false); window.clearTimeout(timer) }
    }
    void load()
    return () => { ignore = true; controller.abort(); window.clearTimeout(timer) }
  }, [page, status, requestId, revision])

  useEffect(() => {
    if (!selected) return
    const controller = new AbortController()
    const timer = window.setTimeout(() => controller.abort(), 15000)
    let ignore = false
    async function load() {
      setDetailLoading(true); setDetailError(''); setDetail(null)
      try {
        const result = await fetchApplicationExecution(selected!, controller.signal)
        if (!ignore) setDetail(result)
      } catch (err) { if (!ignore) setDetailError(formatApiError(err)) }
      finally { if (!ignore) setDetailLoading(false); window.clearTimeout(timer) }
    }
    void load()
    return () => { ignore = true; controller.abort(); window.clearTimeout(timer) }
  }, [selected, revision])

  function changeQuery(next: Record<string, string>) {
    const query = new URLSearchParams(params)
    Object.entries(next).forEach(([key, value]) => value ? query.set(key, value) : query.delete(key))
    setSelected(null); setParams(query)
  }
  const counts = !loading && !error ? data?.status_counts : undefined
  const total = counts ? Object.values(counts).reduce((a, b) => a + b, 0) : undefined
  return <section className="page-stack">
    <header className="page-header"><div><p className="eyebrow">流程追踪</p><h2>每一次申请，都有记录。</h2><p>查看流程状态、核对申请字段，追溯已保存的模拟回执。</p></div>
      <button type="button" className="ghost-button" disabled={loading} onClick={() => setRevision(v => v + 1)}><RefreshCw size={16} className={loading ? 'spin' : ''} aria-hidden="true" />刷新记录</button></header>
    <div className="metric-grid log-metrics" aria-label="全部流程记录统计">
      {[['全部申请', total, '所有已记录的流程申请'], ['模拟完成', counts?.simulated, '有效回执已保存'], ['待核对', counts ? counts.running + counts.timeout : undefined, '执行中或响应超时'], ['执行失败', counts?.failed, '请核对失败原因']].map(([label, value, note], index) =>
        <div className={`metric-card metric-${index}`} key={label}><span>{label}</span><strong>{value ?? '—'}</strong><small>{note}</small></div>)}
    </div>
    <section className="panel log-panel">
      <div className="section-title"><div className="panel-heading"><FileClock size={20} aria-hidden="true" /><div><h3>流程执行记录</h3><p>按最新申请排序 · 当前仅包含流程申请记录</p></div></div><small className="muted-text">{updated && !error ? `更新于 ${formatTime(updated)}` : '等待读取'}</small></div>
      <form className="log-toolbar" onSubmit={event => { event.preventDefault(); const value = new FormData(event.currentTarget).get('request_id'); changeQuery({ request_id: String(value || '').trim(), page: '' }) }}>
        <label className="search-field"><Search size={17} aria-hidden="true" /><input key={requestId} name="request_id" aria-label="申请编号" placeholder="输入完整申请编号查询" defaultValue={requestId} /></label>
        <label className="filter-field"><span className="sr-only">执行状态</span><select aria-label="执行状态" value={status} onChange={event => changeQuery({ status: event.target.value, page: '' })}>
          <option value="">全部状态</option>{Object.entries(statusLabels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
        </select></label><button className="ghost-button" type="submit">查询</button>
        {(status || requestId) && <button type="button" className="text-button" onClick={event => { event.currentTarget.form?.reset(); changeQuery({ status: '', request_id: '', page: '' }) }}>清除筛选</button>}
      </form>
      {error ? <ErrorNotice message={error} retry={() => setRevision(v => v + 1)} /> : loading ? <div className="table-loading" role="status"><RefreshCw size={18} className="spin" />正在读取执行记录…</div> : !data?.items.length ?
        <EmptyState title={requestId || status ? '没有符合条件的记录' : '还没有流程申请'} action={requestId || status ? undefined : <Link to="/workflow" className="primary-button">发起第一份申请<ArrowUpRight size={16} /></Link>}>
          {requestId || status ? '试试其他状态或核对申请编号，也可以清除筛选查看全部。' : '在流程助手核对字段并确认提交后，执行状态和回执会出现在这里。'}
        </EmptyState> : <><div className="table-scroll"><table className="execution-table"><caption className="sr-only">流程申请执行记录</caption><thead><tr><th>申请 / 编号</th><th>申请人</th><th>创建时间</th><th>执行状态</th><th><span className="sr-only">操作</span></th></tr></thead>
          <tbody>{data.items.map(row => <tr key={row.request_id} className={selected === row.request_id ? 'is-selected' : ''}>
            <td><strong>{row.application_type || '未指定类型'}</strong><code title={row.request_id}>{row.request_id.slice(0, 8)}…{row.request_id.slice(-4)}</code></td>
            <td><strong>{row.applicant || '未填写'}</strong><small>{row.department || '未填写部门'}</small></td><td className="time-cell">{formatTime(row.started_at)}</td><td><StatusBadge status={row.status} /></td>
            <td><button type="button" className="text-button" aria-label={`查看${row.request_id}的详情`} onClick={() => setSelected(row.request_id)}>查看详情<ArrowUpRight size={14} aria-hidden="true" /></button></td>
          </tr>)}</tbody></table></div>
          <div className="pagination"><span>共 {data.total} 条 · 第 {page} / {Math.max(1, Math.ceil(data.total / data.page_size))} 页</span><div>
            <button className="ghost-button" type="button" aria-label="上一页" disabled={page <= 1} onClick={() => changeQuery({ page: String(page - 1) })}><ChevronLeft size={16} /></button>
            <button className="ghost-button" type="button" aria-label="下一页" disabled={page * data.page_size >= data.total} onClick={() => changeQuery({ page: String(page + 1) })}><ChevronRight size={16} /></button></div></div>
        </>}
    </section>
    {selected && <section className="panel execution-detail" aria-label="申请执行详情" aria-busy={detailLoading}>
      <div className="section-title"><h3>申请执行详情</h3><button className="ghost-button" type="button" aria-label="关闭详情" onClick={() => setSelected(null)}><X size={17} /></button></div>
      {detailError ? <ErrorNotice message={detailError} retry={() => setRevision(v => v + 1)} /> : detailLoading || !detail || detail.request_id !== selected ? <p role="status">正在读取详情…</p> : <>
        <div className="detail-status"><StatusBadge status={detail.status} /><p>{statusHelp[detail.status]}</p></div>
        <dl className="detail-identifiers"><div><dt>完整申请编号</dt><dd><code>{detail.request_id}</code></dd></div><div><dt>模拟工单编号</dt><dd>{detail.output?.ticket_id || '尚无有效回执'}</dd></div><div><dt>创建 / 完成时间</dt><dd>{formatTime(detail.started_at)} / {formatTime(detail.finished_at)}</dd></div></dl>
        <dl className="detail-fields">{Object.entries(fieldLabels).map(([key, label]) => <div key={key}><dt>{label}</dt><dd>{detail.input.fields[key as keyof typeof fieldLabels] || '未提供'}</dd></div>)}</dl>
        {detail.error_message && <ErrorNotice message={detail.error_message} />}
      </>}
    </section>}
    <p className="page-footnote">仅查询已保存的申请记录，不会重试或重新触发流程。模拟完成不代表实际审批通过、通知送达或权限已授予。</p>
  </section>
}
