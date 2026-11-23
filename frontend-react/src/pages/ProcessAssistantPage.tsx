/** 填写申请，核对系统分配的审批人，再提交工单。 */
import { ArrowLeft, ArrowRight, Check, CheckCircle2, CircleAlert, ClipboardList, Clock3, Plus, Search, UserRoundCheck } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { ApiError } from '../api/httpClient'
import { createTicket, getApprovalRoutes, getTicket, ticketStatusLabels } from '../api/processApi'
import type { ApprovalRoutes, ProcessTicket } from '../api/processApi'
import { SessionActionDialog } from '../components/SessionActionDialog'
import { ScopeSelect } from '../components/ScopeSelect'
import { useWorkspaceAccount } from '../components/WorkspaceAccount'
import '../styles/process-assistant.css'

const STORAGE_KEY = 'enterprise-process-pending-v2'
const STEPS = ['确认申请信息', '提交结果']
const EMPTY_APPLICATION = { applicationType: '', permissionLevel: '', additionalInfo: '' }
function restoreApplication(storageKey: string) {
  try {
    const id = sessionStorage.getItem(storageKey)
    return id && /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(id) ? id : null
  } catch { return null }
}

function errorText(error: unknown, fallback: string): string {
  if (error instanceof ApiError) {
    const detail = (error.payload as { detail?: unknown } | null)?.detail
    if (typeof detail === 'string') return detail
    return `${fallback}（HTTP ${error.status}）`
  }
  return fallback
}

function clearSavedApplication(storageKey: string) {
  try { sessionStorage.removeItem(storageKey) } catch { /* 不影响已经记录的申请。 */ }
}

export function ProcessAssistantPage() {
  const account = useWorkspaceAccount()
  const storageKey = `${STORAGE_KEY}:${account.id}`
  const [pending, setPending] = useState(() => restoreApplication(storageKey))
  const [approvalRoutes, setApprovalRoutes] = useState<ApprovalRoutes | null>(null)
  const [routeError, setRouteError] = useState('')
  const [routeRevision, setRouteRevision] = useState(0)
  const [ticket, setTicket] = useState<ProcessTicket | null>(null)
  const [fields, setFields] = useState(EMPTY_APPLICATION)
  const [busy, setBusy] = useState<'submit' | 'query' | null>(null)
  // 同步锁阻止 React 更新按钮状态之前发生的第二次点击。
  const busyRef = useRef(false)
  const [error, setError] = useState('')
  const [confirmNew, setConfirmNew] = useState(false)
  const newApplicationTrigger = useRef<HTMLButtonElement>(null)
  const step = pending ? 1 : 0
  const resultTone = ticket ? { pending: 'recorded', approved: 'recorded', rejected: 'failed' }[ticket.status]
    : busy === 'submit' ? 'processing' : 'unconfirmed'
  const ResultIcon = resultTone === 'recorded' ? CheckCircle2 : resultTone === 'processing' ? Clock3 : CircleAlert
  const stepHeading = useRef<HTMLHeadingElement>(null)
  const previousStep = useRef(step)
  useEffect(() => {
    if (previousStep.current === step) return
    previousStep.current = step
    stepHeading.current?.focus()
  }, [step])
  const approvalRoute = approvalRoutes?.routes.find(route => route.application_type === fields.applicationType)
  function refreshRoutes() {
    setApprovalRoutes(null)
    setRouteError('')
    setRouteRevision(value => value + 1)
  }
  useEffect(() => {
    if (pending) return
    const controller = new AbortController()
    const signal = AbortSignal.any([controller.signal, AbortSignal.timeout(10000)])
    getApprovalRoutes(signal)
      .then(result => { if (!controller.signal.aborted) setApprovalRoutes(result) })
      .catch(err => { if (!controller.signal.aborted) setRouteError(errorText(err, '暂时无法查询审批人。')) })
    return () => controller.abort()
  }, [routeRevision, pending])

  async function handleSubmit() {
    if (busyRef.current || pending || !approvalRoute || !approvalRoutes) return
    busyRef.current = true
    setBusy('submit')
    setError('')
    let requestId: string
    try {
      requestId = crypto.randomUUID()
      // 必须在发送前保存编号。刷新页面后只查询，不自动重发。
      sessionStorage.setItem(storageKey, requestId)
      setPending(requestId)
    } catch {
      setError('无法在浏览器中保存申请编号，本次未提交。请允许会话存储后重试。')
      busyRef.current = false
      setBusy(null)
      return
    }
    try {
      setTicket(await createTicket({ request_id: requestId,
        fields: { application_type: approvalRoute.application_type, applicant: null, department: null, approver: null,
          permission_level: fields.permissionLevel.trim() || null, additional_info: fields.additionalInfo.trim() || null },
        expected_approver_id: approvalRoute.approver_id, expected_department: approvalRoutes.department }))
      clearSavedApplication(storageKey)
    } catch (err) {
      const conflict = err instanceof ApiError && err.status === 409
        ? (err.payload as { detail?: { code?: string; message?: string } } | null)?.detail : null
      if (err instanceof ApiError && (err.status === 422 || conflict?.code === 'approval_route_changed')) {
        clearSavedApplication(storageKey)
        setPending(null)
        refreshRoutes()
        setError(conflict?.message || errorText(err, '申请字段校验未通过，请核对后重新提交。'))
      }
    } finally { busyRef.current = false; setBusy(null) }
  }

  async function handleQuery() {
    if (busyRef.current || !pending) return
    busyRef.current = true
    setBusy('query')
    setError('')
    try {
      setTicket(await getTicket(pending))
      clearSavedApplication(storageKey)
    } catch (err) {
      setError(err instanceof ApiError && err.status === 404
        ? '暂未查到工单。请稍后再查，或在另建申请前核对我的工单。'
        : '暂时无法查询工单状态，请稍后再试。')
    } finally { busyRef.current = false; setBusy(null) }
  }

  function handleNewApplication() {
    if (busyRef.current || (pending && !ticket && !confirmNew)) return
    clearSavedApplication(storageKey)
    setPending(null)
    setFields(EMPTY_APPLICATION)
    setTicket(null)
    setError('')
    setConfirmNew(false)
  }

  return (
    <section className="page-stack process-assistant">
      <header className="page-header">
        <div className="application-page-heading">
          <Link to="/tickets" className="text-button application-back"><ArrowLeft size={16} aria-hidden="true" />返回工单中心</Link>
          <h2>新建申请</h2>
        </div>
      </header>

      <ol className="application-steps" aria-label="办理进度">
        {STEPS.map((label, index) => (
          <li key={label} aria-current={index === step ? 'step' : undefined} data-complete={index < step}>
            <span className="application-step-number" aria-hidden="true">
              {index < step ? <Check size={15} /> : index + 1}
            </span>
            {label}
          </li>
        ))}
      </ol>

      <section className="panel application-panel" aria-labelledby="application-step-heading">
        <div className="panel-heading" data-result={resultTone} aria-live={pending ? 'polite' : undefined} aria-atomic="true">
          {pending ? <ResultIcon aria-hidden="true" size={21} />
            : <ClipboardList aria-hidden="true" size={21} />}
          <div>
            <h3 id="application-step-heading" ref={stepHeading} tabIndex={-1}>
              {!pending ? '确认申请信息'
                : ticket ? ticketStatusLabels[ticket.status] : busy === 'submit' ? '正在提交申请' : '提交结果待确认'}
            </h3>
            <p>{!pending ? '选择申请类型后，系统会显示对应的审批人。'
              : ticket ? `工单已创建，审批人：${ticket.approver_name}。`
                : busy === 'submit' ? '正在保存工单，请稍候。' : '请先查询原申请状态，避免重复提交。'}</p>
          </div>
        </div>

        {error && <div className="alert error" role="alert">{error}</div>}

        {!pending && <form className="application-form" onSubmit={event => { event.preventDefault(); void handleSubmit() }}>
          <div className="application-fields">
            <label className="field"><span>申请人</span><input value={account.display_name} readOnly /></label>
            <label className="field"><span>所属部门</span><input value={approvalRoutes?.department ?? account.department ?? ''} placeholder="未设置" readOnly /></label>
            <ScopeSelect label="申请类型 *" value={approvalRoute?.application_type ?? ''} icon={<ClipboardList size={17} aria-hidden="true" />}
              disabled={!!busy || !approvalRoutes?.routes.length} placeholder="请选择已配置的申请类型"
              options={(approvalRoutes?.routes ?? []).map(route => ({ label: route.application_type, value: route.application_type }))}
              onChange={value => setFields({ ...fields, applicationType: value })} />
            <label className="field"><span>权限级别 <small>选填</small></span><input value={fields.permissionLevel}
              onChange={event => setFields({ ...fields, permissionLevel: event.target.value })} disabled={!!busy} placeholder="例如只读或读写" /></label>
            <label className="field application-wide"><span>补充信息 <small>选填</small></span>
              <textarea rows={3} value={fields.additionalInfo} disabled={!!busy} placeholder="例如申请原因、日期或时间"
                onChange={event => setFields({ ...fields, additionalInfo: event.target.value })} /></label>
          </div>
          <div className="application-approver" aria-live="polite">
            <UserRoundCheck size={19} aria-hidden="true" />
            <div><strong>审批人</strong><p>{approvalRoute?.approver_name || routeError || (!approvalRoutes ? '正在查询审批规则…'
              : !approvalRoutes.routes.length ? '当前部门没有可用的审批规则，请联系管理员。'
                : '选择申请类型后显示。')}</p></div>
            {(routeError || approvalRoutes?.routes.length === 0) && <button className="text-button" type="button" disabled={!!busy} onClick={refreshRoutes}>重新查询</button>}
          </div>
          <div className="application-actions">
            <button className="primary-button application-primary" type="submit" disabled={!!busy || !approvalRoute}>
              提交审批
            </button>
          </div>
        </form>}

        {pending && <>
          {ticket && <dl className="application-record"><div><dt>工单编号</dt><dd>{ticket.ticket_number}</dd></div>
            {ticket.decision_note && <div><dt>审批意见</dt><dd>{ticket.decision_note}</dd></div>}</dl>}
          <div className="application-actions">
            <Link className="text-button" to="/tickets">查看我的工单 <ArrowRight aria-hidden="true" size={15} /></Link>
            {ticket ? <button className="primary-button application-primary" type="button" onClick={handleNewApplication}>新建申请</button>
              : busy !== 'submit' && <>
                <button ref={newApplicationTrigger} className="ghost-button" type="button" disabled={!!busy}
                  onClick={() => setConfirmNew(true)}><Plus aria-hidden="true" size={16} />另建申请</button>
                <button className="primary-button application-primary" type="button" disabled={!!busy} onClick={() => void handleQuery()}>
                  <Search aria-hidden="true" size={16} />{busy === 'query' ? '正在查询…' : '查询工单状态'}
                </button>
              </>}
          </div>
          {confirmNew && <SessionActionDialog title="确认另建申请" busy={!!busy}
            returnFocusTo={newApplicationTrigger} onClose={() => setConfirmNew(false)}>
            <p>原申请可能已提交。请先核对我的工单，确认需要另建后再继续。</p>
            <div className="session-dialog-actions">
              <button className="ghost-button" type="button" autoFocus onClick={() => setConfirmNew(false)}>取消</button>
              <button className="primary-button" type="button" onClick={handleNewApplication}>确认另建</button>
            </div>
          </SessionActionDialog>}
        </>}
      </section>

    </section>
  )
}
