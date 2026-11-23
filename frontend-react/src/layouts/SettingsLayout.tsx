import { ArrowLeft, Building2, ChevronRight, ClipboardCheck, Users } from 'lucide-react'
import { Link, Navigate, Outlet, useMatch } from 'react-router-dom'
import { useWorkspaceAccount } from '../components/WorkspaceAccount'

const sections = [
  { slug: 'users', title: '用户管理', description: '创建工作台账号，查看部门与角色，管理启停状态。', icon: Users },
  { slug: 'departments', title: '部门管理', description: '维护用户归属和审批规则共用的部门列表。', icon: Building2 },
  { slug: 'approvals', title: '审批与自动化', description: '配置审批人分配规则，查看和重试 n8n 自动化事件。', icon: ClipboardCheck },
]

export function SettingsLayout() {
  const account = useWorkspaceAccount()
  const match = useMatch('/settings/:section')
  const section = sections.find(item => item.slug === match?.params.section)
  if (account.role !== 'admin') return <Navigate to="/" replace />

  return <section className={`page-stack settings-page${section ? ' settings-detail-page' : ' settings-overview-page'}`}>
    <header className="page-header"><h2>{section?.title ?? '设置'}</h2>
      {section && <Link to="/settings" className="ghost-button settings-back"><ArrowLeft size={16} aria-hidden="true" />返回设置</Link>}
    </header>
    <Outlet />
  </section>
}

export function SettingsOverview() {
  return <nav className="settings-overview-list" aria-label="设置模块">
    {sections.map(({ slug, title, description, icon: Icon }) => <Link className="settings-module-link" key={slug} to={slug}>
      <span className="settings-module-icon"><Icon size={20} aria-hidden="true" /></span>
      <span className="settings-module-copy"><strong>{title}</strong><small>{description}</small></span>
      <ChevronRight className="settings-module-arrow" size={18} aria-hidden="true" />
    </Link>)}
  </nav>
}
