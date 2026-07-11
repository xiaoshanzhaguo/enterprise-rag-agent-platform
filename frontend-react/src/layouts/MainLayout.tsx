/* 整个系统的公共页面框架
   负责：
   - 左侧侧边栏
   - 顶部品牌区域
   - 导航菜单
   - 后端 API 状态展示
   - 右侧主内容区域
   - 子路由页面显示位置 Outlet
 */

// 从 lucide-react 图标库里导入三个图标组件。Activity: 活动状态图标；Database：数据库图标；MessagesSquare：对话消息图标
// 新增导航和顶部栏图标：流程、日志、评测、搜索、角色入口。
import { Activity, Database, MessagesSquare, BarChart3, ClipboardList, FileClock, Search, Settings } from 'lucide-react'
// 导入路由组件。NavLink: 导航链接组件，可以根据当前路径自动判断这个链接是不是当前激活状态；Outlet: 子路由页面显示的位置。
import { NavLink, Outlet } from 'react-router-dom'
// 导入后端 API 地址
import { API_BASE_URL } from '../api/httpClient'
import {useState} from "react";

// 左侧导航菜单数组。可以理解为：navigationItems = 左侧菜单配置表
const navigationItems = [
  {
    to: '/chat',
    label: '智能问答',
    description: '员工提问、Agent 路由、RAG 检索',
    icon: MessagesSquare,
  },
  {
    to: '/admin/knowledge',
    label: '知识库管理',
    description: '管理员上传与分类维护入口',
    icon: Database,
  },
  // 流程助手入口，后续承接流程字段提取和 n8n 触发。
  {
    to: '/workflow',
    label: '流程助手',
    description: '流程申请与字段提取',
    icon: ClipboardList,
  },
  // 执行日志入口，后续展示 Agent、RAG、n8n 的全过程记录。
  {
    to: '/logs',
    label: '执行日志',
    description: 'Agent、RAG、n8n 记录',
    icon: FileClock,
  },
  // 评测结果入口，后续展示检索命中、引用命中等质量指标。
  {
    to: '/eval',
    label: '评测结果',
    description: '检索和回答质量指标',
    icon: BarChart3,
  },
]

// 先写死角色选项
const userRoleOptions = [
  {'value': 'employee', label: '普通员工'},
  {'value': 'kb_admin', label: '知识库管理员'},
  {'value': 'approver', label: '审批人'},
  {'value': 'admin', label: '系统管理员'},
]


export function MainLayout() {
  // 选中的角色
  const [selectedRole, setSelectedRole] = useState('employee')

  return (
    <div className="app-shell">
      {/* aside是 HTML5 语义化标签，通常表示侧边栏 */}
      <aside className="sidebar">
        {/* 品牌图标区域 */}
        <div className="brand-block">
          <div className="brand-mark">EA</div>
          <div>
            <p className="eyebrow">Enterprise Agent</p>
            <h1>企业内部知识与流程自动化助手</h1>
          </div>
        </div>

        {/* 导航区域。nav 是 HTML5 语义化标签，表示导航菜单。aria-label="主导航" 是无障碍属性。它的意思是告诉屏幕阅读器：这里是主导航区域，这对访问性更友好。 */}
        <nav className="nav-list" aria-label="主导航">
          {/* map 的作用：把数组里的每一个菜单配置，转换成一个 NavLink 组件 */}
          {navigationItems.map((item) => {
            const Icon = item.icon

            return (
              // NavLink 是 React Router 提供的导航链接，其好处：可以跳转路由，可以自动判断当前链接是否激活
              // className 行的含义：每个导航链接默认都有 nav-item 类名；如果当前链接是激活状态，就额外加 active 类名。
              <NavLink
                key={item.to}
                to={item.to}
                className={({ isActive }) => `nav-item ${isActive ? 'active' : ''}`}
              >
                {/* aria-hidden="true" 表示这个图标对屏幕阅读器隐藏，装饰作用，已有真正的文字说明：{item.label} */}
                {/* size={20} 表示图标大小是 20 像素。这里的 {20} 表示 JSX 里写 JavaScript 数字。 */}
                <Icon aria-hidden="true" size={20} />
                <span>
                  {/* {item.label} 表示把 JavaScript 变量插入 JSX。 */}
                  {/* strong 表示强调文本，通常会显示为加粗。 */}
                  <strong>{item.label}</strong>
                  {/* small 表示较小的辅助文字。 */}
                  <small>{item.description}</small>
                </span>
              </NavLink>
            )
          })}
        </nav>

        {/* 侧边栏底部状态区域，用于展示当前后端服务信息。 */}
        <div className="sidebar-status">
          {/* 渲染一个 Activity 图标 */}
          <Activity aria-hidden="true" size={18} />
          <div>
            <span>FastAPI</span>
            <strong>{API_BASE_URL}</strong>
          </div>
        </div>
      </aside>

      {/* 右侧内容外壳，顶部栏和主内容区都放在这里。 */}
      <div className="content-shell">
        {/* 顶部栏，用于展示控制台标题、搜索入口和用户角色入口。 */}
        <header className="topbar">
          <div>
            <p className="eyebrow">Workspace</p>
            <h2>企业智能助手控制台</h2>
          </div>

          {/* 顶部栏右侧操作区。 */}
          <div className="topbar-actions">
            {/* 搜索入口占位，后续可以接全局搜索。 */}
            <div className="topbar-search">
              <Search aria-hidden="true" size={16} />
              <span>搜索功能待接入</span>
            </div>

            <div className="role-select-control">
              <Settings aria-hidden="true" size={16} />
              <select value={selectedRole} onChange={(event) => setSelectedRole(event.target.value)}>
                {
                  userRoleOptions.map((item) => (
                    <option key={item.value} value={item.value}>
                      {item.label}
                    </option>
                  ))
                }
              </select>
            </div>
          </div>
        </header>

        {/* 右侧主内容区域。main 是 HTML5 语义化标签，表示页面主体内容。 */}
        <main className="main-panel">
          {/* 当前匹配到的子路由页面，显示在这里。 */}
          <Outlet />
        </main>
      </div>
    </div>
  )
}
