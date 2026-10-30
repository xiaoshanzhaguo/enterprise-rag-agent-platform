import { useEffect, useRef, useState } from 'react'
import { ArrowUpRight, Database, MessagesSquare, BarChart3, ClipboardList, FileClock, Layers3, PanelLeftClose, PanelLeftOpen } from 'lucide-react'
import { NavLink, Outlet, useLocation } from 'react-router-dom'

const navigationItems = [
  { to: '/chat', label: '智能问答', icon: MessagesSquare },
  { to: '/admin/knowledge', label: '知识库管理', icon: Database },
  { to: '/workflow', label: '流程助手', icon: ClipboardList },
  { to: '/logs', label: '执行日志', icon: FileClock },
  { to: '/eval', label: '评测结果', icon: BarChart3 },
]
const navigationGroups = [
  { label: '工作空间', items: navigationItems.slice(0, 3) },
  { label: '管理与分析', items: navigationItems.slice(3) },
]

export function MainLayout() {
  const [manuallyCollapsed, setManuallyCollapsed] = useState(false)
  const [hovered, setHovered] = useState(false)
  const [keyboardFocus, setKeyboardFocus] = useState(false)
  const closeTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  useEffect(() => () => { if (closeTimer.current) clearTimeout(closeTimer.current) }, [])
  const preview = manuallyCollapsed && (hovered || keyboardFocus)
  const collapsed = manuallyCollapsed && !preview
  const { pathname } = useLocation()
  const active = navigationItems.find(item => item.to === pathname)

  return <div className={`app-shell${collapsed ? ' sidebar-collapsed' : ''}${preview ? ' sidebar-preview' : ''}`}>
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
      <NavLink to="/chat" className="brand-block" aria-label="知行工作台首页" title={collapsed ? '知行工作台' : undefined}>
        <div className="brand-mark"><Layers3 size={20} aria-hidden="true" /></div>
        <h1>知行工作台</h1>
      </NavLink>
      <button type="button" className="sidebar-toggle" onClick={() => {
        setManuallyCollapsed(!collapsed)
        setHovered(false)
        setKeyboardFocus(false)
      }}
        aria-label={collapsed ? '展开侧边栏' : '收起侧边栏'} title={collapsed ? '展开侧边栏' : '收起侧边栏'}
        aria-expanded={!collapsed} aria-controls="workspace-navigation">
        {collapsed ? <PanelLeftOpen size={19} aria-hidden="true" /> : <PanelLeftClose size={19} aria-hidden="true" />}
      </button>
      </div>
      <nav id="workspace-navigation" className="sidebar-navigation" aria-label="主导航">
        {navigationGroups.map(group => <div className="nav-group" key={group.label}>
          <p className="nav-caption">{group.label}</p>
          <div className="nav-list">{group.items.map(({ to, label, icon: Icon }) =>
            <NavLink key={to} to={to} aria-label={label} title={collapsed ? label : undefined} className={({ isActive }) => `nav-item ${isActive ? 'active' : ''}`}>
              <Icon size={20} aria-hidden="true" /><span>{label}</span>
            </NavLink>)}
          </div>
        </div>)}
      </nav>
    </aside></div>
    <div className="content-shell"><header className="topbar">
      <div className="breadcrumb"><span>企业工作台</span><span aria-hidden="true">/</span><strong>{active?.label || '页面'}</strong></div>
      <div className="topbar-actions"><span className="environment-tag"><i />本地工作空间</span>
        <NavLink className="topbar-shortcut" to="/workflow">发起申请<ArrowUpRight size={15} aria-hidden="true" /></NavLink></div>
    </header><main className="main-panel" id="workspace-main" tabIndex={-1}><Outlet /></main></div>
  </div>
}
