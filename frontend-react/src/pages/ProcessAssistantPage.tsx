/** 流程申请：提取 → 人工确认 → 显式提交 → 查询执行记录。 */
import { CheckCircle2, ClipboardList, Search, SendHorizontal, WandSparkles } from 'lucide-react'
import { useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { EmptyState } from '../components/WorkspaceUI'
import { ApiError } from '../api/httpClient'
import { extractApplicationFields, fetchApplicationExecution, submitApplication } from '../api/processApi'
import type { ProcessFields, ProcessReceipt, ProcessSubmission } from '../types/process'

const STORAGE_KEY = 'enterprise-process-pending-v1'
const fieldDefinitions: { key: keyof ProcessFields; label: string; required?: boolean }[] = [
  { key: 'application_type', label: '申请类型', required: true },
  { key: 'applicant', label: '申请人', required: true },
  { key: 'department', label: '部门', required: true },
  { key: 'permission_level', label: '权限级别' },
  { key: 'approver', label: '审批人' },
  { key: 'additional_info', label: '补充信息' },
]
interface SavedApplication extends ProcessSubmission { input_text: string }

function restoreApplication(): SavedApplication | null {
  try {
    const saved = JSON.parse(sessionStorage.getItem(STORAGE_KEY) || 'null')
    if (!saved || typeof saved.request_id !== 'string'
      || !/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(saved.request_id)
      || typeof saved.input_text !== 'string' || !saved.fields) return null
    if (fieldDefinitions.some(({ key }) => saved.fields[key] !== null && typeof saved.fields[key] !== 'string')) return null
    return saved as SavedApplication
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

function clearSavedApplication() {
  try { sessionStorage.removeItem(STORAGE_KEY) } catch { /* 不影响已经记录的申请。 */ }
}

export function ProcessAssistantPage() {
  const [pending, setPending] = useState<SavedApplication | null>(restoreApplication)
  const [processInput, setProcessInput] = useState(pending?.input_text ?? '')
  const [fields, setFields] = useState<ProcessFields | null>(pending?.fields ?? null)
  const [busy, setBusy] = useState<'extract' | 'submit' | 'query' | null>(null)
  // 同步锁阻止 React 更新按钮状态之前发生的第二次点击。
  const busyRef = useRef(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState(pending ? '已恢复申请编号，请查询执行状态，不要重复提交。' : '')
  const [receipt, setReceipt] = useState<ProcessReceipt | null>(null)
  const [allowNew, setAllowNew] = useState(false)
  const missingRequired = fieldDefinitions.filter(({ key, required }) => required && !fields?.[key]?.trim())

  async function handleExtract() {
    if (busyRef.current || pending || !processInput.trim()) return
    busyRef.current = true
    setBusy('extract')
    setError('')
    setNotice('')
    setFields(null)
    try {
      const result = await extractApplicationFields(processInput.trim())
      if (result.status !== 'extracted') throw new Error('提取失败')
      setFields(result.fields)
      setNotice('字段已提取。请核对姓名、部门和权限，补充后确认提交。')
    } catch (err) {
      setError(errorText(err, '字段提取失败，请检查后端连接后重试。'))
    } finally { busyRef.current = false; setBusy(null) }
  }

  async function handleSubmit() {
    if (busyRef.current || pending || !fields || missingRequired.length) return
    busyRef.current = true
    setBusy('submit')
    setError('')
    setNotice('')
    let application: SavedApplication
    try {
      application = {
        request_id: crypto.randomUUID(), input_text: processInput,
        fields: Object.fromEntries(fieldDefinitions.map(({ key }) => [key, fields[key]?.trim() || null])) as unknown as ProcessFields,
      }
      // 必须在发送前保存编号。刷新页面后只查询，不自动重发。
      sessionStorage.setItem(STORAGE_KEY, JSON.stringify(application))
      setPending(application)
    } catch {
      setError('无法在浏览器中保存申请编号，本次未提交。请允许会话存储后重试。')
      busyRef.current = false
      setBusy(null)
      return
    }
    try {
      const result = await submitApplication({ request_id: application.request_id, fields: application.fields })
      if (result.status !== 'simulated' || result.request_id !== application.request_id || !result.ticket_id) throw new Error('无效回执')
      setReceipt(result)
    } catch (err) {
      if (err instanceof ApiError && err.status === 422) {
        clearSavedApplication()
        setPending(null)
        setError('申请字段校验未通过，本次未触发流程。请检查必填字段后重新提交。')
      } else {
        setError(errorText(err, '连接中断或回执异常，执行结果未知。请查询执行状态，勿重复提交。'))
      }
    } finally { busyRef.current = false; setBusy(null) }
  }

  async function handleQuery() {
    if (busyRef.current || !pending) return
    busyRef.current = true
    setBusy('query')
    setError('')
    setNotice('')
    try {
      const record = await fetchApplicationExecution(pending.request_id)
      if (record.request_id !== pending.request_id) throw new Error('申请编号不匹配')
      if (record.status === 'simulated' && record.output?.request_id === pending.request_id && record.output.ticket_id) {
        setReceipt(record.output)
      } else {
        const descriptions = {
          running: '申请已记录，流程仍在执行或结果尚待核对。稍后可再次查询，请勿重复提交。',
          timeout: '申请已记录，但调用超时，是否执行成功尚不确定。请在 n8n 核对此申请编号。',
          failed: `申请调用失败，失败记录已保存。${record.error_message || ''} 请先核对 n8n 再决定是否另建申请。`,
          simulated: '流程记录存在，但回执不完整，请核对 n8n 执行记录。',
        }
        setNotice(descriptions[record.status] || '执行状态未知，请核对 n8n。')
      }
    } catch (err) {
      setError(err instanceof ApiError && err.status === 404
        ? '暂未查到申请记录，不能据此确认未执行。请核对后端和 n8n 后再决定是否另建申请。'
        : errorText(err, '执行状态查询失败，已保留申请编号，请稍后再次查询。'))
    } finally { busyRef.current = false; setBusy(null) }
  }

  function handleNewApplication() {
    if (busyRef.current || (pending && !receipt && !allowNew)) return
    clearSavedApplication()
    setPending(null)
    setFields(null)
    setProcessInput('')
    setReceipt(null)
    setError('')
    setNotice('')
    setAllowNew(false)
  }

  return (
    <section className="page-stack">
      <header className="page-header">
        <div>
          <p className="eyebrow">流程侧 · 申请办理</p>
          <h2>流程助手</h2>
          <p>描述申请需求，核对字段后提交，并查看流程执行记录。</p>
        </div>
      </header>
      <ol className="process-steps" aria-label="办理步骤">
        <li><span>01</span> 描述需求</li><li><span>02</span> 核对字段</li><li><span>03</span> 提交与记录</li>
      </ol>
      <div className="workspace-grid">
        <section className="panel process-form-panel">
          <div className="panel-heading">
            <ClipboardList aria-hidden="true" size={20} />
            <div><h3>流程申请草稿</h3><p>字段提取不会提交申请；确认后才触发流程。</p></div>
          </div>
          <label className="field">
            <span>申请描述</span>
            <textarea rows={5} maxLength={20000} value={processInput} disabled={!!busy || !!pending}
              placeholder="例如：我是研发部张三，申请 VPN 只读权限，用于远程办公，审批人为李经理。"
              onChange={(event) => { setProcessInput(event.target.value); setFields(null); setError(''); setNotice('') }} />
          </label>
          <button className="primary-button" type="button" onClick={handleExtract} disabled={!processInput.trim() || !!busy || !!pending}>
            <WandSparkles aria-hidden="true" size={18} />{busy === 'extract' ? '正在提取字段…' : fields ? '重新提取字段' : '提取申请字段'}
          </button>
          {fields && <section className="process-extraction-panel" aria-label="申请字段">
            <div className="process-extraction-heading"><WandSparkles aria-hidden="true" size={18} /><div>
              <strong>核对申请字段</strong><span>标记 * 的字段为提交必填项，其余可留空。</span>
            </div></div>
            <div className="process-field-list">
              {fieldDefinitions.map(({ key, label, required }) => <label className="field" key={key}>
                <span>{label}{required ? ' *' : ''}</span>
                {key === 'additional_info' ? <textarea aria-label={label} rows={3} value={fields[key] ?? ''} disabled={!!busy || !!pending}
                  placeholder="原因、用途、时间等补充信息，可选填"
                  onChange={(event) => setFields({ ...fields, [key]: event.target.value || null })} /> : <input aria-label={label} required={required} value={fields[key] ?? ''} disabled={!!busy || !!pending}
                  placeholder={required ? '请补充' : '未提供，可选填'}
                  onChange={(event) => setFields({ ...fields, [key]: event.target.value || null })} />}
              </label>)}
            </div>
            {!pending && missingRequired.length > 0 && <p className="muted-text">待补充：{missingRequired.map(f => f.label).join('、')}。</p>}
            <button className="primary-button" type="button" onClick={handleSubmit} disabled={!!busy || !!pending || missingRequired.length > 0}>
              <SendHorizontal aria-hidden="true" size={18} />{busy === 'submit' ? '正在提交，请勿重复操作…' : pending ? '本申请已提交，勿重复提交' : '确认提交'}
            </button>
          </section>}
          {error && <div className="alert error" role="alert">{error}</div>}
          {notice && <p className="process-notice" role="status">{notice}</p>}
        </section>
        <aside className="panel process-result-panel" aria-label="流程执行结果">
          <div className="panel-heading"><Search aria-hidden="true" size={20} /><div><h3>流程执行结果</h3><p>提交后可查询已保存的执行状态。</p></div></div>
          {receipt ? <div className="process-success" role="status">
            <CheckCircle2 aria-hidden="true" size={24} /><div><strong>流程已触发 / 已记录</strong><p>模拟流程执行完成，回执已保存，可在执行日志中查看。</p></div>
          </div> : pending ? <p className="muted-text">已保留申请编号。结果不明时先查询，不自动重发。</p> : <EmptyState title="等待确认提交">完成字段核对后，申请编号与执行结果会显示在这里。</EmptyState>}
          {pending && <>
            <dl className="process-record-details"><div><dt>申请编号</dt><dd>{pending.request_id}</dd></div>
              <div><dt>模拟工单编号</dt><dd>{receipt?.ticket_id || '等待有效回执'}</dd></div></dl>
            <button className="primary-button" type="button" disabled={!!busy} onClick={handleQuery}>
              <Search aria-hidden="true" size={16} />{busy === 'query' ? '正在查询…' : '查询执行状态'}
            </button>
            <Link className="text-button" to={`/logs?request_id=${encodeURIComponent(pending.request_id)}`}>在执行日志中查看此申请 →</Link>
            {!receipt && <label className="process-new-confirm"><input type="checkbox" checked={allowNew} disabled={!!busy}
              onChange={event => setAllowNew(event.target.checked)} />我已核对原申请结果，确认需要另建申请。</label>}
            <button className="process-secondary-button" type="button" onClick={handleNewApplication} disabled={!!busy || (!receipt && !allowNew)}>新建另一份申请</button>
          </>}
          <p className="process-simulation-note">当前为模拟流程：生成表格记录、通知内容和模拟工单，不会真实发送通知或授予权限。</p>
        </aside>
      </div>
    </section>
  )
}
