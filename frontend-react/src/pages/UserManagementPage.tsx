import { useContext, useEffect, useRef, useState } from 'react'
import { Building2, Pencil, UserRound } from 'lucide-react'
import { formatApiError } from '../api/httpClient'
import { createUser, listDepartments, listUsers, setUserEnabled, updateUser, workspaceModules } from '../api/workspaceApi'
import type { ManagedWorkspaceUser, WorkspaceDepartment, WorkspaceModule, WorkspaceUser } from '../api/workspaceApi'
import { useWorkspaceAccount, WorkspaceAccountUpdate } from '../components/WorkspaceAccount'
import { SessionActionDialog } from '../components/SessionActionDialog'
import { ScopeSelect } from '../components/ScopeSelect'

export function UserManagementPage() {
  const account = useWorkspaceAccount()
  const updateAccount = useContext(WorkspaceAccountUpdate)
  const [users, setUsers] = useState<ManagedWorkspaceUser[] | null>(null)
  const [departments, setDepartments] = useState<WorkspaceDepartment[]>([])
  const [department, setDepartment] = useState('')
  const [role, setRole] = useState<WorkspaceUser['role']>('member')
  const [error, setError] = useState('')
  const [formError, setFormError] = useState('')
  const [editor, setEditor] = useState<ManagedWorkspaceUser | 'new' | null>(null)
  const [permissions, setPermissions] = useState<WorkspaceModule[]>([])
  const [confirmDisable, setConfirmDisable] = useState<WorkspaceUser | null>(null)
  const [busy, setBusy] = useState(false)
  const trigger = useRef<HTMLButtonElement>(null)
  const switchTrigger = useRef<HTMLButtonElement>(null)
  const busyRef = useRef(false)
  const editing = editor && editor !== 'new' ? editor : null

  useEffect(() => {
    let active = true
    Promise.all([listUsers(), listDepartments()]).then(([items, options]) => {
      if (active) { setUsers(items); setDepartments(options) }
    }).catch(err => {
      if (active) setError(formatApiError(err))
    })
    return () => { active = false }
  }, [])

  function openEditor(button: HTMLButtonElement, user?: ManagedWorkspaceUser) {
    trigger.current = button
    setFormError('')
    setDepartment(String(departments.find(item => item.name === user?.department)?.id ?? ''))
    setRole(user?.role ?? 'member')
    setPermissions(user?.module_permissions ?? workspaceModules.map(module => module.id))
    setEditor(user ?? 'new')
  }

  async function save(data: FormData) {
    if (busyRef.current || !editor || (!editing && !department)) return
    busyRef.current = true
    setBusy(true)
    setFormError('')
    try {
      const profile = {
        username: String(data.get('username')).trim(),
        display_name: String(data.get('display_name')).trim(),
        work_email: String(data.get('work_email')).trim() || null,
        role,
        module_permissions: permissions,
      }
      const saved = editing
        ? await updateUser(editing.id, { ...profile, department_id: department ? Number(department) : null })
        : await createUser({ ...profile, department_id: Number(department), password: String(data.get('password')) })
      setUsers(items => editing ? items?.map(user => user.id === saved.id ? saved : user) ?? null : [...(items ?? []), saved])
      if (saved.id === account.id) updateAccount(saved)
      setEditor(null)
    } catch (err) { setFormError(formatApiError(err)) }
    finally { busyRef.current = false; setBusy(false) }
  }

  async function toggle(user: WorkspaceUser, enabled: boolean) {
    if (busyRef.current) return
    busyRef.current = true
    setBusy(true)
    setFormError('')
    try {
      const updated = await setUserEnabled(user.id, enabled)
      setUsers(items => items?.map(item => item.id === updated.id ? updated : item) ?? null)
      setConfirmDisable(null)
    } catch (err) {
      if (enabled) setError(formatApiError(err))
      else setFormError(formatApiError(err))
    } finally { busyRef.current = false; setBusy(false) }
  }

  return <section className="page-stack">
    {error && <div className="alert error" role="alert">{error}</div>}
    <section className="panel settings-panel" aria-label="用户列表">
      <div className="settings-list-toolbar">
        <button className="primary-button" type="button" disabled={!users || busy}
          onClick={event => openEditor(event.currentTarget)}>新建用户</button></div>
      {users ? <div className="workspace-user-panel"><table className="workspace-users">
        <thead><tr><th scope="col">姓名</th><th scope="col">用户名</th><th scope="col">工作邮箱</th><th scope="col">所属部门</th><th scope="col">角色</th>
          <th scope="col">状态</th><th scope="col">操作</th></tr></thead>
        <tbody>{users.map(user => <tr key={user.id}><td>{user.display_name}</td><td>{user.username}</td>
          <td>{user.work_email || '未配置'}</td><td>{user.department || '未分配'}</td><td>{user.role === 'admin' ? '管理员' : '员工'}</td>
          <td><button className="user-status-switch" role="switch" aria-checked={user.enabled} aria-label={`${user.display_name}的启用状态`}
            title={user.id === account.id ? '当前登录账号' : undefined} type="button" disabled={busy || user.id === account.id}
            onClick={event => {
              if (!user.enabled) { setError(''); void toggle(user, true); return }
              switchTrigger.current = event.currentTarget
              setFormError('')
              setConfirmDisable(user)
            }}><span className="user-switch-track" aria-hidden="true" /><span>{user.enabled ? '启用' : '停用'}</span></button></td>
          <td><button className="settings-row-action" type="button" disabled={busy} aria-label={`编辑${user.display_name}`}
            onClick={event => openEditor(event.currentTarget, user)}><Pencil size={14} aria-hidden="true" />编辑</button></td></tr>)}</tbody>
      </table></div> : !error && <p role="status">正在加载用户…</p>}
    </section>
    {editor && <SessionActionDialog title={editing ? '编辑用户' : '新建用户'} busy={busy} returnFocusTo={trigger} onClose={() => setEditor(null)}>
      <form onSubmit={event => { event.preventDefault(); void save(new FormData(event.currentTarget)) }}>
        {formError && <div className="alert error" role="alert">{formError}</div>}
        <label className="field"><span>姓名</span><input name="display_name" defaultValue={editing?.display_name} required maxLength={80} disabled={busy} /></label>
        <label className="field"><span>用户名</span><input name="username" defaultValue={editing?.username} required
          pattern={String.raw`[\p{Script=Han}a-zA-Z0-9_.\-]{2,64}`}
          title="可使用中文、英文字母、数字、点、下划线和连字符" disabled={busy} /></label>
        <label className="field"><span>工作邮箱（选填）</span><input name="work_email" type="email" autoComplete="email"
          defaultValue={editing?.work_email ?? ''} maxLength={254} disabled={busy} /></label>
        {!editing && <label className="field"><span>初始密码（至少 8 个字符）</span><input name="password" type="password" autoComplete="new-password" required minLength={8} maxLength={128} disabled={busy} /></label>}
        <ScopeSelect label={editing ? '所属部门' : '所属部门 *'} value={department} icon={<Building2 size={16} aria-hidden="true" />} disabled={busy || !departments.length}
          options={departments.map(item => ({ value: String(item.id), label: item.name }))} onChange={setDepartment} />
        {!departments.length && <p className="settings-empty">请先到「部门管理」新增部门。</p>}
        <ScopeSelect label="角色" value={role} icon={<UserRound size={16} aria-hidden="true" />} disabled={busy || editing?.id === account.id}
          options={[{ value: 'member', label: '员工' }, { value: 'admin', label: '管理员' }]}
          onChange={value => setRole(value === 'admin' ? 'admin' : 'member')} />
        <fieldset className="user-module-options" disabled={busy}>
          <legend>模块权限</legend>
          {workspaceModules.map(({ id, label }) => <label key={id} className="user-module-option">
            <input type="checkbox" role="switch" checked={permissions.includes(id)}
              onChange={event => {
                const checked = event.target.checked
                setPermissions(current => checked ? [...current, id] : current.filter(module => module !== id))
              }} />
            <span className="user-switch-track" aria-hidden="true" /><span>{label}</span>
          </label>)}
        </fieldset>
        <div className="session-dialog-actions">
          <button type="button" className="ghost-button" disabled={busy} onClick={() => setEditor(null)}>取消</button>
          <button type="submit" className="primary-button" disabled={busy || (!editing && !department)}>
            {busy ? '正在保存…' : editing ? '保存修改' : '创建用户'}
          </button>
        </div>
      </form>
    </SessionActionDialog>}
    {confirmDisable && <SessionActionDialog title="停用账号" busy={busy} returnFocusTo={switchTrigger} onClose={() => setConfirmDisable(null)}>
      {formError && <div className="alert error" role="alert">{formError}</div>}
      <p>确认停用「{confirmDisable.display_name}」？账号将退出登录，重新启用后可以再次登录。</p>
      <div className="session-dialog-actions"><button type="button" className="ghost-button" autoFocus disabled={busy} onClick={() => setConfirmDisable(null)}>取消</button>
        <button type="button" className="primary-button danger-solid-button" disabled={busy}
          onClick={() => void toggle(confirmDisable, false)}>确认停用</button></div>
    </SessionActionDialog>}
  </section>
}
