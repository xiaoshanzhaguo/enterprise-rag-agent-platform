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
import { Activity, Database, MessagesSquare } from 'lucide-react'
// 导入路由组件。NavLink: 导航链接组件，可以根据当前路径自动判断这个链接是不是当前激活状态；Outlet: 子路由页面显示的位置。
import { NavLink, Outlet } from 'react-router-dom'
// 导入后端 API 地址
import { API_BASE_URL } from '../api/httpClient'

// 左侧导航菜单数组。可以理解为：navigationItems = 左侧菜单配置表
const navigationItems = [
  {
    to: '/chat',
    label: '企业问答',
    description: '员工提问、Agent 路由、RAG 检索',
    icon: MessagesSquare,
  },
  {
    to: '/admin/knowledge',
    label: '知识库管理',
    description: '管理员上传与分类维护入口',
    icon: Database,
  },
]

export function MainLayout() {
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

      {/* 右侧主内容区域。main 是 HTML5 语义化标签，表示页面主体内容。 */}
      <main className="main-panel">
        {/* 当前匹配到的子路由页面，显示在这里。 */}
        <Outlet />
      </main>
    </div>
  )
}
