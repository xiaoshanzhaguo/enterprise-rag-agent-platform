import { useEffect, useRef, useState } from 'react'
import { ArrowRight, Bell, ChevronDown, ClipboardCheck, ClipboardList, Plus, RefreshCw } from 'lucide-react'
import { Link, NavLink, useOutletContext } from 'react-router-dom'
import { formatApiError } from '../api/httpClient'
import { decideTicket, listTickets, ticketStatusLabels } from '../api/processApi'
import type { ProcessTicket } from '../api/processApi'
import { SessionActionDialog } from '../components/SessionActionDialog'
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
  const [decision, setDecision] = useState<{ ticket: ProcessTicket; value: 'approved' | 'rejected' } | null>(null)
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)
  const trigger = useRef<HTMLButtonElement>(null)
  const submitting = useRef(false)
  const assigned = view === 'assigned'
  const pending = tickets?.filter(ticket => ticket.status === 'pending') ?? []
  const finished = tickets?.filter(ticket => ticket.status !== 'pending') ?? []

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
    try {
      const updated = await decideTicket(decision.ticket.request_id, decision.value, note.trim())
      setTickets(items => items?.map(item => item.request_id === updated.request_id ? updated : item) ?? null)
      setDecision(null)
    } catch (err) {
      setDecision(null)
      setError(formatApiError(err, '审批结果待确认，请刷新列表。'))
    } finally { submitting.current = false; setBusy(false); refreshPendingApprovals() }
  }

  function ticketRow(ticket: ProcessTicket) {
    return <details className="ticket-row" key={ticket.request_id}>
      <summary>
        <div className="ticket-identity"><strong>{ticket.fields.application_type}</strong><code>{ticket.ticket_number}</code></div>
        <span className="ticket-party">{assigned ? `${ticket.fields.applicant} · ${ticket.fields.department}` : `审批人 · ${ticket.approver_name}`}</span>
        <span className="ticket-status" data-status={ticket.status}>{ticketStatusLabels[ticket.status]}</span>
        <ChevronDown size={16} className="ticket-chevron" aria-hidden="true" />
      </summary>
      <div className="ticket-details">
        <dl className="application-record">
          <div><dt>申请人 / 部门</dt><dd>{ticket.fields.applicant} · {ticket.fields.department}</dd></div>
          <div><dt>审批人</dt><dd>{ticket.approver_name}</dd></div>
          <div><dt>提交时间</dt><dd>{displayTicketTime(ticket.created_at)}</dd></div>
          {ticket.decided_at && <div><dt>审批时间</dt><dd>{displayTicketTime(ticket.decided_at)}</dd></div>}
          {ticket.fields.permission_level && <div><dt>权限级别</dt><dd>{ticket.fields.permission_level}</dd></div>}
          {ticket.fields.additional_info && <div><dt>补充信息</dt><dd>{ticket.fields.additional_info}</dd></div>}
          {ticket.decision_note && <div><dt>审批意见</dt><dd>{ticket.decision_note}</dd></div>}
        </dl>
        {ticket.can_decide && <div className="session-dialog-actions">
          <button type="button" className="ghost-button" onClick={event => {
            trigger.current = event.currentTarget; setNote(''); setDecision({ ticket, value: 'rejected' })
          }}>驳回</button>
          <button type="button" className="primary-button" onClick={event => {
            trigger.current = event.currentTarget; setNote(''); setDecision({ ticket, value: 'approved' })
          }}>通过</button>
        </div>}
      </div>
    </details>
  }

  return <section className="page-stack ticket-center">
    <header className="page-header"><div><h2>工单中心</h2><p>查看申请进度，处理分配给你的审批。</p></div>
      {account.module_permissions.includes('workflow') && <Link to="/tickets/new" className="primary-button application-link"><Plus size={17} aria-hidden="true" />新建申请</Link>}
    </header>
    {pendingApprovals > 0 && <div className="ticket-pending-notice">
      <Bell size={19} aria-hidden="true" />
      <p role="status">你有 <strong>{pendingApprovals}</strong> 张工单待审批</p>
      {!assigned && <Link to="/tickets/approvals" className="text-button">查看待审批 <ArrowRight size={15} aria-hidden="true" /></Link>}
    </div>}
    <section className="panel ticket-panel" aria-label={assigned ? '我的审批' : '我的工单'}>
      <div className="ticket-toolbar">
        <nav aria-label="工单中心导航" className="ticket-nav">
          <NavLink to="/tickets" end><ClipboardList size={17} aria-hidden="true" />我的工单</NavLink>
          {account.module_permissions.includes('tickets') && <NavLink to="/tickets/approvals"><ClipboardCheck size={17} aria-hidden="true" />我的审批</NavLink>}
        </nav>
        <button type="button" className="ghost-button ticket-refresh" onClick={reload} disabled={busy || (!tickets && !error)}>
          <RefreshCw size={16} aria-hidden="true" />刷新
        </button>
      </div>
      {error && <div className="alert error" role="alert">{error}</div>}
      {!tickets && !error && <p className="ticket-empty" role="status">正在加载…</p>}
      {tickets?.length === 0 && <p className="ticket-empty">{assigned ? '暂无分配给你的审批。' : '你还没有提交工单。'}</p>}
      {pending.length > 0 && <section className="ticket-group" aria-label="待审批">
        <h3>{assigned ? '待我审批' : '待审批'}</h3>{pending.map(ticketRow)}
      </section>}
      {finished.length > 0 && <section className="ticket-group" aria-label="已处理">
        <h3>已处理</h3>{finished.map(ticketRow)}
      </section>}
    </section>
    {decision && <SessionActionDialog title={decision.value === 'approved' ? '确认通过工单' : '确认驳回工单'}
      busy={busy} returnFocusTo={trigger} onClose={() => setDecision(null)}>
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
