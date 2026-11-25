import { useEffect, useRef, useState } from 'react'
import { ArrowRight, Bell, Plus, RefreshCw } from 'lucide-react'
import { Link, NavLink, useOutletContext } from 'react-router-dom'
import { formatApiError } from '../api/httpClient'
import { decideTicket, listTickets, ticketStatusLabels } from '../api/processApi'
import type { ProcessTicket } from '../api/processApi'
import { SessionActionDialog } from '../components/SessionActionDialog'
import { DEFAULT_TABLE_PAGE_SIZE, TablePagination } from '../components/TablePagination'
import { useWorkspaceAccount } from '../components/WorkspaceAccount'
import type { WorkspaceOutletContext } from '../layouts/MainLayout'
import '../styles/process-assistant.css'

const ticketTimeFormat = new Intl.DateTimeFormat('zh-CN', { dateStyle: 'medium', timeStyle: 'short' })

function displayTicketTime(timestamp: string) {
  const date = new Date(timestamp.replace(' ', 'T') + 'Z')
  return ticketTimeFormat.format(date)
}

export function ProcessTicketsPage({ view }: { view: 'mine' | 'assigned' }) {
  const account = useWorkspaceAccount()
  const { pendingApprovals, refreshPendingApprovals } = useOutletContext<WorkspaceOutletContext>()
  const [tickets, setTickets] = useState<ProcessTicket[] | null>(null)
  const [revision, setRevision] = useState(0)
  const [error, setError] = useState('')
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(DEFAULT_TABLE_PAGE_SIZE)
  const [detailId, setDetailId] = useState<string | null>(null)
  const [decision, setDecision] = useState<{ ticket: ProcessTicket; value: 'approved' | 'rejected' } | null>(null)
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)
  const detailTrigger = useRef<HTMLButtonElement>(null)
  const decisionTrigger = useRef<HTMLButtonElement>(null)
  const submitting = useRef(false)
  const assigned = view === 'assigned'
  const orderedTickets = [...(tickets ?? [])].sort((left, right) =>
    Number(right.status === 'pending') - Number(left.status === 'pending') || right.ticket_number.localeCompare(left.ticket_number, undefined, { numeric: true }))
  const currentPage = Math.min(page, Math.max(1, Math.ceil(orderedTickets.length / pageSize)))
  const visibleTickets = orderedTickets.slice((currentPage - 1) * pageSize, currentPage * pageSize)
  const detail = tickets?.find(ticket => ticket.request_id === detailId)
  const listLabel = assigned ? '我的审批' : '我的工单'

  useEffect(() => {
    let active = true
    listTickets(view).then(items => { if (active) setTickets(items) }).catch(err => {
      if (active) setError(formatApiError(err))
    })
    return () => { active = false }
  }, [view, revision])

  function reload() {
    setTickets(null)
    setError('')
    setRevision(value => value + 1)
    refreshPendingApprovals()
  }

  async function saveDecision() {
    if (!decision || submitting.current || (decision.value === 'rejected' && !note.trim())) return
    submitting.current = true
    setBusy(true)
    setError('')
    try {
      const updated = await decideTicket(decision.ticket.request_id, decision.value, note.trim())
      setTickets(items => items?.map(item => item.request_id === updated.request_id ? updated : item) ?? null)
      setDecision(null)
    } catch (err) {
      setDecision(null)
      setDetailId(null)
      setError(formatApiError(err, '审批结果待确认，请刷新列表。'))
    } finally { submitting.current = false; setBusy(false); refreshPendingApprovals() }
  }

  return <section className="page-stack ticket-center">
    <header className="page-header"><div><h2>工单中心</h2><p>查看申请进度，处理分配给你的审批。</p></div></header>
    {pendingApprovals > 0 && <div className="ticket-pending-notice">
      <Bell size={19} aria-hidden="true" />
      <p role="status">你有 <strong>{pendingApprovals}</strong> 张工单待审批</p>
      {!assigned && <Link to="/tickets/approvals" className="text-button">查看待审批 <ArrowRight size={15} aria-hidden="true" /></Link>}
    </div>}
    <nav className="workspace-tabs" aria-label="工单中心视图">
      <NavLink to="/tickets" end>我的工单</NavLink>
      {account.module_permissions.includes('tickets') && <NavLink to="/tickets/approvals">我的审批</NavLink>}
    </nav>
    <section className="panel ticket-panel" aria-label={listLabel}>
      <div className="workspace-panel-actions">
        {account.module_permissions.includes('workflow') && <Link to="/tickets/new" className="primary-button application-link">
          <Plus size={17} aria-hidden="true" />新建申请
        </Link>}
        <button type="button" className="ghost-button ticket-refresh" onClick={reload} disabled={busy || (!tickets && !error)}>
          <RefreshCw size={16} aria-hidden="true" />刷新
        </button>
      </div>
      {error && <div className="alert error" role="alert">{error}</div>}
      {!tickets && !error && <p className="ticket-empty" role="status">正在加载…</p>}
      {tickets?.length === 0 && <p className="ticket-empty">{assigned ? '暂无分配给你的审批。' : '你还没有提交工单。'}</p>}
      {visibleTickets.length > 0 && <>
        <div className="admin-table-scroll" tabIndex={0} role="region" aria-label={`${listLabel}列表`}>
          <table className="admin-table ticket-table">
            <caption className="sr-only">{listLabel}</caption>
            <thead><tr>
              <th scope="col">工单编号</th><th scope="col">申请类型</th>
              <th scope="col">{assigned ? '申请人 / 部门' : '审批人'}</th>
              <th scope="col">提交时间</th><th scope="col">状态</th><th scope="col">操作</th>
            </tr></thead>
            <tbody>{visibleTickets.map(ticket => <tr key={ticket.request_id}>
              <td><code className="ticket-number">{ticket.ticket_number}</code></td>
              <th scope="row">{ticket.fields.application_type}</th>
              <td>{assigned ? <>{ticket.fields.applicant}<small>{ticket.fields.department}</small></> : ticket.approver_name}</td>
              <td><time dateTime={ticket.created_at.replace(' ', 'T') + 'Z'}>{displayTicketTime(ticket.created_at)}</time></td>
              <td><span className="ticket-status" data-status={ticket.status}>{ticketStatusLabels[ticket.status]}</span></td>
              <td><button type="button" className="text-button" onClick={event => {
                detailTrigger.current = event.currentTarget
                setDetailId(ticket.request_id)
              }}>{assigned && ticket.can_decide ? '处理审批' : '查看详情'}</button></td>
            </tr>)}</tbody>
          </table>
        </div>
        <TablePagination label={`${listLabel}分页`} total={orderedTickets.length} page={currentPage} pageSize={pageSize}
          onPageChange={setPage} onPageSizeChange={size => { setPageSize(size); setPage(1) }} />
      </>}
    </section>
    {detail && <SessionActionDialog title="工单详情" busy={busy} returnFocusTo={detailTrigger} onClose={() => setDetailId(null)}>
      <div className="ticket-details">
        <section aria-labelledby="ticket-application-title">
          <h4 id="ticket-application-title">申请信息</h4>
          <dl className="ticket-description">
            <div><dt>工单编号</dt><dd><code className="ticket-number">{detail.ticket_number}</code></dd></div>
            <div><dt>申请类型</dt><dd>{detail.fields.application_type}</dd></div>
            <div><dt>申请人</dt><dd>{detail.fields.applicant}</dd></div>
            <div><dt>所属部门</dt><dd>{detail.fields.department}</dd></div>
            <div><dt>提交时间</dt><dd>{displayTicketTime(detail.created_at)}</dd></div>
            {detail.fields.permission_level && <div><dt>权限级别</dt><dd>{detail.fields.permission_level}</dd></div>}
            {detail.fields.additional_info && <div><dt>补充信息</dt><dd>{detail.fields.additional_info}</dd></div>}
          </dl>
        </section>
        <section aria-labelledby="ticket-approval-title">
          <h4 id="ticket-approval-title">审批信息</h4>
          <dl className="ticket-description">
            <div><dt>审批状态</dt><dd><span className="ticket-status" data-status={detail.status}>{ticketStatusLabels[detail.status]}</span></dd></div>
            <div><dt>审批人</dt><dd>{detail.approver_name}</dd></div>
            {detail.decided_at && <div><dt>审批时间</dt><dd>{displayTicketTime(detail.decided_at)}</dd></div>}
            {detail.decision_note && <div><dt>审批意见</dt><dd>{detail.decision_note}</dd></div>}
          </dl>
        </section>
        {assigned && detail.can_decide && <div className="session-dialog-actions">
          <button type="button" className="ghost-button" onClick={event => {
            decisionTrigger.current = event.currentTarget; setNote(''); setDecision({ ticket: detail, value: 'rejected' })
          }}>驳回</button>
          <button type="button" className="primary-button" onClick={event => {
            decisionTrigger.current = event.currentTarget; setNote(''); setDecision({ ticket: detail, value: 'approved' })
          }}>通过</button>
        </div>}
      </div>
    </SessionActionDialog>}
    {decision && <SessionActionDialog title={decision.value === 'approved' ? '确认通过工单' : '确认驳回工单'}
      busy={busy} returnFocusTo={decisionTrigger} onClose={() => setDecision(null)}>
      <p>{decision.ticket.ticket_number} · {decision.ticket.fields.application_type}</p>
      <label className="field"><span>{decision.value === 'rejected' ? '驳回原因（必填）' : '审批意见（选填）'}</span>
        <textarea value={note} rows={3} maxLength={500} disabled={busy} onChange={event => setNote(event.target.value)} /></label>
      <div className="session-dialog-actions">
        <button type="button" className="ghost-button" disabled={busy} onClick={() => setDecision(null)}>取消</button>
        <button type="button" className="primary-button" disabled={busy || (decision.value === 'rejected' && !note.trim())}
          onClick={() => void saveDecision()}>{busy ? '正在保存…' : '确认'}</button>
      </div>
    </SessionActionDialog>}
  </section>
}
