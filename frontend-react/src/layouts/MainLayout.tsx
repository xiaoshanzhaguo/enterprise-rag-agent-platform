import { useContext, useEffect, useRef, useState } from 'react'
import { BarChart3, Database, FileClock, Layers3, ListChecks, LogOut, MessageSquareText, MessagesSquare, Pin, Settings } from 'lucide-react'
import { NavLink, Outlet, useLocation } from 'react-router-dom'
import { useWorkspaceAccount, WorkspaceSignOut } from '../components/WorkspaceAccount'
import { canOpenModule, workspaceNavigation } from '../api/workspaceApi'
import { getPendingApprovalCount } from '../api/processApi'

const icons = { chat: MessagesSquare, tickets: ListChecks, knowledge: Database, logs: FileClock, evaluation: BarChart3, feedback: MessageSquareText }
const navigationGroups = [
  { label: '工作空间', items: workspaceNavigation.slice(0, 3) },
  { label: '管理与分析', items: [...workspaceNavigation.slice(3), { id: 'feedback', path: '/feedback', label: '问答反馈' } as const] },
]
export interface WorkspaceOutletContext {
  pendingApprovals: number
  refreshPendingApprovals: () => void
}

export function MainLayout() {
  const account = useWorkspaceAccount()
  const { pathname } = useLocation()
  const canApprove = account.module_permissions.includes('tickets')
  const [approvalCount, setApprovalCount] = useState<{ accountId: number; value: number } | null>(null)
  const [approvalRevision, setApprovalRevision] = useState(0)
  const pendingApprovals = canApprove && approvalCount?.accountId === account.id ? approvalCount.value : 0
  const refreshPendingApprovals = () => setApprovalRevision(value => value + 1)
  useEffect(() => {
    if (!canApprove) return
    let request: AbortController | undefined
    async function refresh() {
      if (document.hidden) return
      request?.abort()
      const controller = new AbortController()
      request = controller
      try {
        const { pending_count } = await getPendingApprovalCount(
          AbortSignal.any([controller.signal, AbortSignal.timeout(10000)]),
        )
        if (!controller.signal.aborted) setApprovalCount({ accountId: account.id, value: pending_count })
      } catch { /* 保留最近确认的数量，留待下次查询。 */ }
    }
    void refresh()
    const timer = window.setInterval(refresh, 30000)
    document.addEventListener('visibilitychange', refresh)
    return () => {
      request?.abort()
      window.clearInterval(timer)
      document.removeEventListener('visibilitychange', refresh)
    }
  }, [account.id, canApprove, pathname, approvalRevision])
  const groups = navigationGroups.map(group => ({ ...group, items: group.items.filter(item =>
    item.id === 'feedback' ? account.role === 'admin' : canOpenModule(account.module_permissions, item.id),
  ) }))
    .filter(group => group.items.length)
  const signOut = useContext(WorkspaceSignOut)
  const [pinned, setPinned] = useState(() => {
    try { return localStorage.getItem('workspace-sidebar-pinned') !== '0' }
    catch { return true }
  })
  const [hovered, setHovered] = useState(false)
  const [keyboardFocus, setKeyboardFocus] = useState(false)
  const closeTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  useEffect(() => () => { if (closeTimer.current) clearTimeout(closeTimer.current) }, [])
  const collapsed = !(pinned || hovered || keyboardFocus)
  function togglePin() {
    const next = !pinned
    setPinned(next)
    try { localStorage.setItem('workspace-sidebar-pinned', next ? '1' : '0') }
    catch { /* 存储不可用时仍可切换固定状态。 */ }
  }

  return <div className={`app-shell${collapsed ? ' sidebar-collapsed' : ''}${pinned ? ' sidebar-pinned' : ''}`}>
    <a href="#workspace-main" className="skip-link">跳到主要内容</a>
    <div className="sidebar-rail"><aside className="sidebar"
      onPointerEnter={event => {
        if (event.pointerType === 'mouse') {
          if (closeTimer.current) clearTimeout(closeTimer.current)
          setHovered(true)
        }
      }}
      onPointerLeave={event => {
        if (event.pointerType === 'mouse') closeTimer.current = setTimeout(() => setHovered(false), 160)
      }}
      onFocusCapture={event => { if (event.target.matches(':focus-visible')) setKeyboardFocus(true) }}
      onBlurCapture={event => { if (!event.currentTarget.contains(event.relatedTarget)) setKeyboardFocus(false) }}
      onKeyDown={event => {
        if (event.key === 'Escape') { setHovered(false); setKeyboardFocus(false) }
      }}>
      <div className="sidebar-heading">
      <NavLink to="/" className="brand-block" aria-label="知行工作台首页" title={collapsed ? '知行工作台' : undefined}>
        <div className="brand-mark"><Layers3 size={20} aria-hidden="true" /></div>
        <h1>知行工作台</h1>
      </NavLink>
      <button type="button" className="sidebar-toggle" onClick={togglePin}
        aria-label={pinned ? '取消固定侧栏' : '固定侧栏展开'} title={pinned ? '取消固定后，移开鼠标自动收起' : '固定侧栏展开'}
        aria-pressed={pinned} aria-expanded={!collapsed} aria-controls="workspace-navigation">
        <Pin size={17} aria-hidden="true" />
      </button>
      </div>
      <nav id="workspace-navigation" className="sidebar-navigation" aria-label="主导航">
        {groups.map(group => <div className="nav-group" key={group.label}>
          <p className="nav-caption">{group.label}</p>
          <div className="nav-list">{group.items.map(({ id, path, label }) => {
            const Icon = icons[id]
            const hasPending = id === 'tickets' && pendingApprovals > 0
            const accessLabel = hasPending ? `${label}，${pendingApprovals} 张待审批` : label
            return <NavLink key={id} to={path} aria-label={accessLabel} title={collapsed ? accessLabel : undefined} className={({ isActive }) => `nav-item ${isActive ? 'active' : ''}`}>
              <Icon size={20} aria-hidden="true" /><span>{label}</span>
              {hasPending && <small className="nav-pending-badge" aria-hidden="true">{pendingApprovals > 99 ? '99+' : pendingApprovals}</small>}
            </NavLink>})}
          </div>
        </div>)}
      </nav>
      <div className="sidebar-account">
        {account.role === 'admin' && <NavLink to="/settings" aria-label="设置" title="设置"
          className={({ isActive }) => `nav-item sidebar-settings-link${isActive ? ' active' : ''}`}>
          <Settings size={20} aria-hidden="true" /><span>设置</span>
        </NavLink>}
        <button type="button" className="nav-item sidebar-sign-out" aria-label="退出登录" title={`${account.display_name} · 退出登录`}
          onClick={signOut ?? undefined} disabled={!signOut}>
          <LogOut size={20} aria-hidden="true" /><span>退出登录</span>
        </button>
      </div>
    </aside></div>
    <div className="content-shell"><main className="main-panel" id="workspace-main" tabIndex={-1}>
      <Outlet context={{ pendingApprovals, refreshPendingApprovals } satisfies WorkspaceOutletContext} />
    </main></div>
  </div>
}
