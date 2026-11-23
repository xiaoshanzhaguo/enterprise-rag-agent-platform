import { useEffect, useRef, useState } from 'react'
import { Building2, Pencil, RefreshCw, Trash2, UserRoundCheck } from 'lucide-react'
import { deleteApprovalRule, listApprovalRules, listTicketEvents, retryTicketEvent, saveApprovalRule, updateApprovalRule } from '../api/processApi'
import type { ApprovalRule, TicketEvent } from '../api/processApi'
import { listDepartments, listUsers } from '../api/workspaceApi'
import type { WorkspaceDepartment, WorkspaceUser } from '../api/workspaceApi'
import { formatApiError } from '../api/httpClient'
import { ScopeSelect } from '../components/ScopeSelect'
import { SessionActionDialog } from '../components/SessionActionDialog'

const eventStatus = { pending: '待发送', sending: '发送中', sent: '已收到回执', failed: '待核对' }

export function ApprovalSettingsPage() {
  const [users, setUsers] = useState<WorkspaceUser[] | null>(null)
  const [departments, setDepartments] = useState<WorkspaceDepartment[]>([])
  const [department, setDepartment] = useState('')
  const [rules, setRules] = useState<ApprovalRule[] | null>(null)
  const [events, setEvents] = useState<TicketEvent[] | null>(null)
  const [editor, setEditor] = useState<{ rule?: ApprovalRule; remove?: boolean } | null>(null)
  const [retryEvent, setRetryEvent] = useState<TicketEvent | null>(null)
  const [approver, setApprover] = useState('')
  const [revision, setRevision] = useState(0)
  const [error, setError] = useState('')
  const [formError, setFormError] = useState('')
  const [notice, setNotice] = useState('')
  const [busy, setBusy] = useState(false)
  const busyRef = useRef(false)
  const trigger = useRef<HTMLButtonElement>(null)

  useEffect(() => {
    let active = true
    Promise.all([listUsers(), listDepartments(), listApprovalRules(), listTicketEvents()]).then(([accounts, options, configured, deliveries]) => {
      if (active) { setUsers(accounts); setDepartments(options); setRules(configured); setEvents(deliveries) }
    }).catch(err => { if (active) setError(formatApiError(err)) })
    return () => { active = false }
  }, [revision])

  function openEditor(button: HTMLButtonElement, rule?: ApprovalRule, remove = false) {
    trigger.current = button
    setDepartment('')
    setApprover(rule?.approver_enabled ? String(rule.approver_id) : '')
    setFormError('')
    setEditor({ rule, remove })
  }

  function refresh() {
    setError('')
    setRevision(value => value + 1)
  }

  async function saveRule(data: FormData) {
    if (!editor || busyRef.current) return
    busyRef.current = true
    setBusy(true)
    setFormError('')
    try {
      if (editor.remove && editor.rule) {
        await deleteApprovalRule(editor.rule.id)
      } else if (editor.rule) {
        await updateApprovalRule(editor.rule.id, Number(approver))
      } else {
        await saveApprovalRule({ department_id: Number(department),
          application_type: String(data.get('application_type')).trim(), approver_id: Number(approver) })
      }
      setEditor(null)
      refresh()
    } catch (err) { setFormError(formatApiError(err)) }
    finally { busyRef.current = false; setBusy(false) }
  }

  async function retry(event: TicketEvent) {
    if (busyRef.current) return
    busyRef.current = true
    setBusy(true)
    setNotice('')
    setError('')
    try {
      await retryTicketEvent(event.event_id, event.attempts)
      setNotice(`${event.ticket_number} 的自动化已安排发送，稍后可刷新查看回执。`)
      refresh()
    } catch (err) { setError(formatApiError(err)) }
    finally { busyRef.current = false; setBusy(false) }
  }

  return <section className="page-stack">
    {error && <div className="alert error" role="alert">{error}</div>}
    <section className="panel settings-panel" aria-labelledby="approval-rules-title">
      <div className="settings-panel-heading"><h3 id="approval-rules-title">审批规则</h3>
        <button className="primary-button" type="button" disabled={!users || !departments.length || busy} onClick={event => openEditor(event.currentTarget)}>新增规则</button></div>
      {!rules?.length ? <p className="settings-empty">{rules ? (departments.length ? '暂无审批规则。' : '请先到「部门管理」新增部门。') : !error && '正在加载规则…'}</p>
        : <div className="workspace-user-panel"><table className="workspace-users">
          <thead><tr><th scope="col">所属部门</th><th scope="col">申请类型</th><th scope="col">审批人</th><th scope="col">操作</th></tr></thead>
          <tbody>{rules.map(rule => <tr key={rule.id}><td>{rule.department}</td><td>{rule.application_type}</td>
            <td>{rule.approver_name}{!rule.approver_enabled && '（不可审批）'}</td>
            <td><div className="settings-actions">
              <button type="button" className="settings-row-action" disabled={busy} onClick={event => openEditor(event.currentTarget, rule)}>
                <Pencil size={14} aria-hidden="true" />编辑</button>
              <button type="button" className="settings-row-action settings-row-action-danger" disabled={busy} onClick={event => openEditor(event.currentTarget, rule, true)}>
                <Trash2 size={14} aria-hidden="true" />删除</button>
            </div></td></tr>)}</tbody>
        </table></div>}
    </section>
    <section className="panel settings-panel" aria-labelledby="automation-title">
      <div className="settings-panel-heading"><h3 id="automation-title">工单自动化记录</h3>
        <button className="ghost-button ticket-refresh" type="button" disabled={busy} onClick={refresh}><RefreshCw size={16} aria-hidden="true" />刷新</button></div>
      {notice && <p className="settings-empty" role="status">{notice}</p>}
      {!events?.length ? <p className="settings-empty">{events ? '暂无发送记录。' : !error && '正在加载记录…'}</p>
        : <div className="workspace-user-panel"><table className="workspace-users">
          <thead><tr><th scope="col">工单</th><th scope="col">触发事件</th><th scope="col">发送状态</th><th scope="col">操作</th></tr></thead>
          <tbody>{events.map(event => <tr key={event.event_id}><td>{event.ticket_number}</td>
            <td>{{ 'process_ticket.submitted': '提交审批', 'process_ticket.approved': '审批通过', 'process_ticket.rejected': '审批驳回' }[event.event_type]}</td>
            <td><span className="ticket-status" data-status={event.status === 'sent' ? 'approved' : event.status === 'failed' ? 'rejected' : 'pending'}>{eventStatus[event.status]}</span>
              {event.error_message && <details className="automation-error"><summary>执行详情</summary>
                <p>{event.error_message}</p><code>{event.event_id}</code></details>}</td>
            <td>{event.status === 'pending' || event.status === 'failed' ? <button className="text-button" disabled={busy}
              type="button" onClick={button => {
                trigger.current = button.currentTarget
                setRetryEvent(event)
              }}>{event.status === 'pending' ? '发送' : '核对后重试'}</button> : <span>{event.attempts} 次尝试</span>}</td></tr>)}</tbody>
        </table></div>}
    </section>
    {retryEvent && <SessionActionDialog title="确认发送自动化事件" busy={busy} returnFocusTo={trigger} onClose={() => setRetryEvent(null)}>
      <p>如先前发送结果未知，请先到自动化平台按事件编号核对执行记录。</p>
      <p><code className="automation-event-id">{retryEvent.event_id}</code></p>
      <div className="session-dialog-actions">
        <button type="button" className="ghost-button" disabled={busy} onClick={() => setRetryEvent(null)}>取消</button>
        <button type="button" className="primary-button" disabled={busy} onClick={() => {
          void retry(retryEvent)
          setRetryEvent(null)
        }}>确认发送</button>
      </div>
    </SessionActionDialog>}
    {editor && <SessionActionDialog title={editor.remove ? '删除审批规则' : editor.rule ? '编辑审批规则' : '新增审批规则'}
      busy={busy} returnFocusTo={trigger} onClose={() => setEditor(null)}>
      <form onSubmit={event => { event.preventDefault(); void saveRule(new FormData(event.currentTarget)) }}>
        {formError && <div className="alert error" role="alert">{formError}</div>}
        {editor.remove ? <p>删除「{editor.rule?.department} · {editor.rule?.application_type}」的分配规则？</p> : <>
          {editor.rule ? <label className="field"><span>所属部门</span><input value={editor.rule.department} readOnly /></label>
            : <ScopeSelect label="所属部门" value={department} icon={<Building2 size={16} aria-hidden="true" />} disabled={busy}
              options={departments.map(item => ({ value: String(item.id), label: item.name }))} onChange={setDepartment} />}
          <label className="field"><span>申请类型</span><input name="application_type" required maxLength={100}
            defaultValue={editor.rule?.application_type ?? ''} readOnly={Boolean(editor.rule)} disabled={busy} placeholder="例如 VPN 权限" /></label>
          <ScopeSelect label="审批人" icon={<UserRoundCheck size={16} aria-hidden="true" />} value={approver} disabled={busy}
            options={(users ?? []).filter(user => user.enabled && user.module_permissions.includes('tickets'))
              .map(user => ({ value: String(user.id), label: user.display_name, description: user.username }))}
            onChange={setApprover} />
        </>}
        <div className="session-dialog-actions">
          <button type="button" className="ghost-button" disabled={busy} onClick={() => setEditor(null)}>取消</button>
          <button type="submit" className={`primary-button${editor.remove ? ' danger-solid-button' : ''}`}
            disabled={busy || (!editor.remove && (!approver || (!editor.rule && !department)))}>{editor.remove ? '确认删除' : '保存规则'}</button>
        </div>
      </form>
    </SessionActionDialog>}
  </section>
}
