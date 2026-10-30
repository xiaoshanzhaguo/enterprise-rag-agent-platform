import { ArrowUpRight, Database, MessagesSquare, BarChart3, ClipboardList, FileClock, Layers3 } from 'lucide-react'
import { NavLink, Outlet, useLocation } from 'react-router-dom'

const navigationItems = [
  { to: '/chat', label: '智能问答', icon: MessagesSquare, subtitle: '有据可查的企业知识' },
  { to: '/admin/knowledge', label: '知识库管理', icon: Database, subtitle: '文档与分类维护' },
  { to: '/workflow', label: '流程助手', icon: ClipboardList, subtitle: '从需求到申请' },
  { to: '/logs', label: '执行日志', icon: FileClock, subtitle: '记录与回执追踪' },
  { to: '/eval', label: '评测结果', icon: BarChart3, subtitle: '回答质量与证据' },
]

export function MainLayout() {
  const { pathname } = useLocation()
  const active = navigationItems.find(item => item.to === pathname)

  return <div className="app-shell">
    <a href="#workspace-main" className="skip-link">跳到主要内容</a>
    <aside className="sidebar">
      <NavLink to="/chat" className="brand-block" aria-label="企业知识与流程工作台首页">
        <div className="brand-mark"><Layers3 size={24} aria-hidden="true" /></div>
        <div><p className="eyebrow">ENTERPRISE / WORKSPACE</p><h1>知行工作台</h1></div>
      </NavLink>
      <div><p className="nav-caption">工作空间</p><nav className="nav-list" aria-label="主导航">
        {navigationItems.map(({ to, label, icon: Icon, subtitle }) =>
          <NavLink key={to} to={to} className={({ isActive }) => `nav-item ${isActive ? 'active' : ''}`}>
            <Icon size={19} aria-hidden="true" /><span><strong>{label}</strong><small>{subtitle}</small></span>
          </NavLink>)}
      </nav></div>
    </aside>
    <div className="content-shell"><header className="topbar">
      <div className="breadcrumb"><span>企业工作台</span><span aria-hidden="true">/</span><strong>{active?.label || '页面'}</strong></div>
      <div className="topbar-actions"><span className="environment-tag"><i />本地工作空间</span>
        <NavLink className="topbar-shortcut" to="/workflow">发起申请<ArrowUpRight size={15} aria-hidden="true" /></NavLink></div>
    </header><main className="main-panel" id="workspace-main" tabIndex={-1}><Outlet /></main></div>
  </div>
}
