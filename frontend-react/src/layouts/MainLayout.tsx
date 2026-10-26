import { Activity, ArrowUpRight, Database, MessagesSquare, BarChart3, ClipboardList, FileClock, Layers3 } from 'lucide-react'
import { NavLink, Outlet, useLocation } from 'react-router-dom'
import { useEffect, useState } from 'react'
import { apiGet } from '../api/httpClient'

const navigationItems = [
  { to: '/chat', label: '智能问答', icon: MessagesSquare, subtitle: '有据可查的企业知识' },
  { to: '/admin/knowledge', label: '知识库管理', icon: Database, subtitle: '文档与分类维护' },
  { to: '/workflow', label: '流程助手', icon: ClipboardList, subtitle: '从需求到申请' },
  { to: '/logs', label: '执行日志', icon: FileClock, subtitle: '记录与回执追踪' },
  { to: '/eval', label: '评测结果', icon: BarChart3, subtitle: '回答质量与证据' },
]

export function MainLayout() {
  const [health, setHealth] = useState<'checking' | 'online' | 'offline'>('checking')
  const [check, setCheck] = useState(0)
  const { pathname } = useLocation()
  const active = navigationItems.find(item => item.to === pathname)
  useEffect(() => {
    const controller = new AbortController()
    let ignore = false
    const timer = window.setTimeout(() => controller.abort(), 8000)
    apiGet<{ status: string }>('/health', controller.signal)
      .then(result => { if (!ignore) setHealth(result.status === 'ok' ? 'online' : 'offline') })
      .catch(() => { if (!ignore) setHealth('offline') })
      .finally(() => window.clearTimeout(timer))
    return () => { ignore = true; controller.abort(); window.clearTimeout(timer) }
  }, [check])

  return <div className="app-shell">
    <a href="#workspace-main" className="skip-link">跳到主要内容</a>
    <aside className="sidebar">
      <NavLink to="/chat" className="brand-block" aria-label="企业知识与流程工作台首页">
        <div className="brand-mark"><Layers3 size={24} aria-hidden="true" /></div>
        <div><p className="eyebrow">ENTERPRISE / WORKSPACE</p><h1>知行工作台</h1></div>
      </NavLink>
      <div><p className="nav-caption">工作空间</p><nav className="nav-list" aria-label="主导航">
        {navigationItems.map(({ to, label, icon: Icon, subtitle }, index) =>
          <NavLink key={to} to={to} className={({ isActive }) => `nav-item ${isActive ? 'active' : ''}`}>
            <Icon size={19} aria-hidden="true" /><span><strong>{label}</strong><small>{subtitle}</small></span>
            <small className="nav-number">0{index + 1}</small>
          </NavLink>)}
      </nav></div>
      <div className="sidebar-bottom"><div className="sidebar-note"><span>知识有据，流程有迹。</span><p>企业内部知识与流程协作空间</p></div>
        <button type="button" className={`sidebar-status health-${health}`} onClick={() => { setHealth('checking'); setCheck(v => v + 1) }} aria-label="重新检查后端连接">
          <Activity size={17} aria-hidden="true" /><span>后端服务</span><strong>{health === 'online' ? '已连接' : health === 'checking' ? '检查中' : '未连接'}</strong>
        </button></div>
    </aside>
    <div className="content-shell"><header className="topbar">
      <div className="breadcrumb"><span>企业工作台</span><span aria-hidden="true">/</span><strong>{active?.label || '页面'}</strong></div>
      <div className="topbar-actions"><span className="environment-tag"><i />本地工作空间</span>
        <NavLink className="topbar-shortcut" to="/workflow">发起申请<ArrowUpRight size={15} aria-hidden="true" /></NavLink></div>
    </header><main className="main-panel" id="workspace-main" tabIndex={-1}><Outlet /></main></div>
  </div>
}
