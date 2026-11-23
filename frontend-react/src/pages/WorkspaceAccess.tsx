import { useEffect, useState } from 'react'
import type { ReactNode } from 'react'
import { Eye, EyeOff, Layers3 } from 'lucide-react'
import { ApiError, formatApiError } from '../api/httpClient'
import { getWorkspaceUser, login, logout } from '../api/workspaceApi'
import type { WorkspaceUser } from '../api/workspaceApi'
import { WorkspaceAccount, WorkspaceAccountUpdate, WorkspaceSignOut } from '../components/WorkspaceAccount'
import '../styles/workspace-account.css'

export function WorkspaceAccess({ children }: { children: ReactNode }) {
  const [account, setAccount] = useState<WorkspaceUser | null>(null)
  const [loading, setLoading] = useState(true)
  const [attempt, setAttempt] = useState(0)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [showPassword, setShowPassword] = useState(false)

  useEffect(() => {
    let active = true
    getWorkspaceUser().then(user => { if (active) setAccount(user) }).catch(err => {
      if (active && !(err instanceof ApiError && err.status === 401)) setError(formatApiError(err))
    }).finally(() => { if (active) setLoading(false) })
    return () => { active = false }
  }, [attempt])

  async function signIn(data: FormData) {
    if (busy) return
    setBusy(true)
    setError('')
    try {
      setAccount(await login(String(data.get('username')), String(data.get('password'))))
      setShowPassword(false)
    } catch (err) {
      setError(formatApiError(err, '登录失败，请重试。'))
    } finally { setBusy(false) }
  }

  async function signOut() {
    if (busy) return
    setBusy(true)
    setError('')
    try { await logout(); setAccount(null) }
    catch (err) { setError(formatApiError(err, '退出失败，请重试。')) }
    finally { setBusy(false) }
  }

  if (account) return <WorkspaceAccount.Provider key={account.id} value={account}>
    {error && <div className="account-banner alert error" role="alert">{error}</div>}
    <WorkspaceAccountUpdate.Provider value={setAccount}>
      <WorkspaceSignOut.Provider value={busy ? null : () => void signOut()}>{children}</WorkspaceSignOut.Provider>
    </WorkspaceAccountUpdate.Provider>
  </WorkspaceAccount.Provider>

  return <main className="account-login"><section className="account-login-card" aria-labelledby="account-login-title">
    <header className="account-login-heading">
      <span className="brand-mark"><Layers3 size={20} aria-hidden="true" /></span>
      <h1 id="account-login-title">知行工作台</h1>
    </header>
    {error && <div className="alert error" role="alert">{error}</div>}
    {loading ? <p className="account-login-status" role="status">正在检查登录状态…</p> : <form className="account-form" onSubmit={event => {
      event.preventDefault()
      void signIn(new FormData(event.currentTarget))
    }}>
      <label className="field"><span>用户名</span><input name="username" autoComplete="username" maxLength={128} required /></label>
      <div className="field">
        <label htmlFor="workspace-password"><span>密码</span></label>
        <div className="account-password">
          <input id="workspace-password" name="password" type={showPassword ? 'text' : 'password'} autoComplete="current-password" maxLength={128} required />
          <button className="account-password-toggle" type="button" aria-label={showPassword ? '隐藏密码' : '显示密码'}
            onClick={() => setShowPassword(visible => !visible)}>
            {showPassword ? <EyeOff size={18} aria-hidden="true" /> : <Eye size={18} aria-hidden="true" />}
          </button>
        </div>
      </div>
      <div className="account-login-actions">
        <button className="primary-button" type="submit" disabled={busy}>{busy ? '正在登录…' : '登录'}</button>
        {error && <button className="text-button" type="button" disabled={busy}
          onClick={() => { setError(''); setLoading(true); setAttempt(value => value + 1) }}>重新检查连接</button>}
      </div>
    </form>}
  </section></main>
}
