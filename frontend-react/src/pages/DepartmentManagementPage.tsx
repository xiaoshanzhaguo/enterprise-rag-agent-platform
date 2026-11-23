import { useEffect, useRef, useState } from 'react'
import { Pencil, Trash2 } from 'lucide-react'
import { formatApiError } from '../api/httpClient'
import { createDepartment, deleteDepartment, listDepartments, renameDepartment } from '../api/workspaceApi'
import type { WorkspaceDepartment } from '../api/workspaceApi'
import { SessionActionDialog } from '../components/SessionActionDialog'

type DepartmentAction = { type: 'create' } | { type: 'rename' | 'delete'; department: WorkspaceDepartment }

export function DepartmentManagementPage() {
  const [departments, setDepartments] = useState<WorkspaceDepartment[] | null>(null)
  const [action, setAction] = useState<DepartmentAction | null>(null)
  const [revision, setRevision] = useState(0)
  const [loadError, setLoadError] = useState('')
  const [formError, setFormError] = useState('')
  const [busy, setBusy] = useState(false)
  const busyRef = useRef(false)
  const trigger = useRef<HTMLButtonElement>(null)

  useEffect(() => {
    let active = true
    listDepartments().then(items => { if (active) setDepartments(items) })
      .catch(error => { if (active) setLoadError(formatApiError(error)) })
    return () => { active = false }
  }, [revision])

  function refresh() {
    setLoadError('')
    setRevision(value => value + 1)
  }

  function open(button: HTMLButtonElement, next: DepartmentAction) {
    trigger.current = button
    setFormError('')
    setAction(next)
  }

  async function save(data: FormData) {
    if (!action || busyRef.current) return
    busyRef.current = true
    setBusy(true)
    setFormError('')
    const name = String(data.get('name') ?? '').trim()
    try {
      if (action.type === 'create') await createDepartment(name)
      else if (action.type === 'rename') await renameDepartment(action.department.id, name)
      else await deleteDepartment(action.department.id)
      setAction(null)
      refresh()
    } catch (error) { setFormError(formatApiError(error)) }
    finally { busyRef.current = false; setBusy(false) }
  }

  const title = action?.type === 'delete' ? '删除部门' : action?.type === 'rename' ? '重命名部门' : '新增部门'
  return <section className="panel settings-panel" aria-label="部门列表">
    <div className="settings-list-toolbar">
      <button className="primary-button" type="button" disabled={busy} onClick={event => open(event.currentTarget, { type: 'create' })}>新增部门</button></div>
    {loadError && <div className="alert error" role="alert">{loadError} <button type="button" className="text-button" onClick={refresh}>重试</button></div>}
    {!departments && !loadError && <p className="settings-empty" role="status">正在加载部门…</p>}
    {departments?.length === 0 && <p className="settings-empty">暂无部门。新增部门后，可在创建用户和审批规则时选择。</p>}
    {departments && departments.length > 0 && <div className="workspace-user-panel"><table className="workspace-users">
      <thead><tr><th scope="col">部门名称</th><th scope="col">用户数</th><th scope="col">审批规则</th><th scope="col">操作</th></tr></thead>
      <tbody>{departments.map(department => <tr key={department.id}>
        <td>{department.name}</td><td>{department.user_count}</td><td>{department.rule_count}</td>
        <td><div className="settings-actions">
          <button type="button" className="settings-row-action" disabled={busy} onClick={event => open(event.currentTarget, { type: 'rename', department })}>
            <Pencil size={14} aria-hidden="true" />重命名</button>
          {department.user_count || department.rule_count ? <span className="department-in-use">使用中</span>
            : <button type="button" className="settings-row-action settings-row-action-danger" disabled={busy}
              onClick={event => open(event.currentTarget, { type: 'delete', department })}><Trash2 size={14} aria-hidden="true" />删除</button>}
        </div></td>
      </tr>)}</tbody>
    </table></div>}
    {action && <SessionActionDialog title={title} busy={busy} returnFocusTo={trigger} onClose={() => setAction(null)}>
      <form onSubmit={event => { event.preventDefault(); void save(new FormData(event.currentTarget)) }}>
        {formError && <div className="alert error" role="alert">{formError}</div>}
        {action.type === 'delete' ? <p>确认删除「{action.department.name}」部门？</p>
          : <label className="field"><span>部门名称</span><input name="name" defaultValue={action.type === 'rename' ? action.department.name : ''}
            disabled={busy} required maxLength={80} /></label>}
        <div className="session-dialog-actions">
          <button className="ghost-button" type="button" autoFocus={action.type === 'delete'} disabled={busy} onClick={() => setAction(null)}>取消</button>
          <button className={`primary-button${action.type === 'delete' ? ' danger-solid-button' : ''}`} type="submit" disabled={busy}>
            {action.type === 'delete' ? '确认删除' : '保存'}
          </button>
        </div>
      </form>
    </SessionActionDialog>}
  </section>
}
