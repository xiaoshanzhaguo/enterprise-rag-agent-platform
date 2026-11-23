// BrowserRouter: 表示启用浏览器路由，它负责监听浏览器地址栏，例如 /chat，然后根据地址栏路径决定显示哪个页面。
// Routes: 表示一组路由规则的容器，可以理解为：Routes 里面放很多 Route。
// Route: 表示一条具体路由规则。比如：<Route path=/chat" element={<ChatPage />} />，意思是：当地址栏是 /chat 时，显示 ChatPage 页面。
// Navigate: 表示重定向，也就是自动跳转。比如：<Navigate to="/chat" replace />，意思是：自动跳转到 /chat
// replace 的意思是：跳转到 /chat 时，替换当前这条浏览器历史记录，而不是新增一条历史记录。

import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom'
import { canOpenModule, workspaceNavigation } from '../api/workspaceApi'
import type { WorkspaceModule } from '../api/workspaceApi'
import { useWorkspaceAccount } from '../components/WorkspaceAccount'
import type { ReactNode } from 'react'
import { MainLayout } from '../layouts/MainLayout'
import { SettingsLayout, SettingsOverview } from '../layouts/SettingsLayout'
import { ChatWorkspacePage } from '../pages/ChatWorkspacePage'
// 评测结果页面。
import { EvaluationResultPage } from '../pages/EvaluationResultPage'
// 执行日志页面。
import { ExecutionLogPage } from '../pages/ExecutionLogPage'
import { KnowledgeAdminPage } from '../pages/KnowledgeAdminPage'
import { NotFoundPage } from '../pages/NotFoundPage'
// 流程助手页面。
import { ProcessAssistantPage } from '../pages/ProcessAssistantPage'
import { WorkspaceAccess } from '../pages/WorkspaceAccess'
import { UserManagementPage } from '../pages/UserManagementPage'
import { ApprovalSettingsPage } from '../pages/ApprovalSettingsPage'
import { FeedbackReviewPanel } from '../components/FeedbackReviewPanel'
import { DepartmentManagementPage } from '../pages/DepartmentManagementPage'
import { ProcessTicketsPage } from '../pages/ProcessTicketsPage'

function WorkspaceHome() {
  const account = useWorkspaceAccount()
  const first = workspaceNavigation.find(module => canOpenModule(account.module_permissions, module.id))
  if (first) return <Navigate to={first.path} replace />
  if (account.role === 'admin') return <Navigate to="/settings" replace />
  return <p role="status">当前账号尚未开通工作台模块，请联系管理员。</p>
}

function ModuleAccess({ module, children, exact = false }: { module: WorkspaceModule; children: ReactNode; exact?: boolean }) {
  const { module_permissions: permissions } = useWorkspaceAccount()
  const allowed = exact ? permissions.includes(module) : canOpenModule(permissions, module)
  return allowed ? children : <p role="alert">当前账号未开通此模块。</p>
}

function FeedbackAccess() {
  return useWorkspaceAccount().role === 'admin' ? <FeedbackReviewPanel /> : <Navigate to="/" replace />
}

// 定义一个 React 函数组件，并将这个组件导出
export function AppRouter() {
  // 表示 AppRouter 组件要返回一段 JSX。JSX 是 React 里用来描述页面结构的语法。
  // 这里返回的是一套路由配置。
  return (
    // 启用浏览器路由。可以理解为：从这里开始，里面的 Router 才能根据地址栏路径工作。它会监听浏览器地址栏。
    // 比如当前地址为：http://localhost:5173/chat，它就知道当前路径是 /chat ，然后交给里面的 Routes 去匹配。
    <BrowserRouter>
      {/*可以理解为：Routes: 路由规则列表；Route: 列表里的某一条规则。*/}
      <Routes>
        {/*定义一个父级路由。含义：下面这些子路由，都共用 MainLayout 这个布局。即 /chat 等都会先显示 MainLayout，然后再在 MainLayout 里面显示具体页面。*/}
        <Route element={<WorkspaceAccess><MainLayout /></WorkspaceAccess>}>
          {/*默认子路由：访问 / 时跳转到 /chat。index 表示默认页面，因为它写在内部，因此它表示 MainLayout 下的默认子页面。*/}
          {/*整行理解：用户访问首页 / 时，自动跳转到 /chat，并且不要在浏览器历史里保留 /。*/}
          <Route index element={<WorkspaceHome />} />
          <Route path="/chat" element={<ModuleAccess module="chat"><ChatWorkspacePage /></ModuleAccess>} />
          <Route path="/admin/knowledge" element={<ModuleAccess module="knowledge"><KnowledgeAdminPage /></ModuleAccess>} />
          <Route path="/workflow" element={<Navigate to="/tickets/new" replace />} />
          <Route path="/workflow/tickets" element={<Navigate to="/tickets" replace />} />
          <Route path="/tickets" element={<ModuleAccess module="tickets"><ProcessTicketsPage key="mine" view="mine" /></ModuleAccess>} />
          <Route path="/tickets/new" element={<ModuleAccess module="workflow"><ProcessAssistantPage /></ModuleAccess>} />
          <Route path="/tickets/approvals" element={<ModuleAccess module="tickets" exact><ProcessTicketsPage key="assigned" view="assigned" /></ModuleAccess>} />
          <Route path="/feedback" element={<FeedbackAccess />} />
          <Route path="/settings" element={<SettingsLayout />}>
            <Route index element={<SettingsOverview />} />
            <Route path="users" element={<UserManagementPage />} />
            <Route path="departments" element={<DepartmentManagementPage />} />
            <Route path="approvals" element={<ApprovalSettingsPage />} />
            <Route path="feedback" element={<Navigate to="/feedback" replace />} />
          </Route>
          {/* 执行日志路由。 */}
          <Route path="/logs" element={<ModuleAccess module="logs"><ExecutionLogPage /></ModuleAccess>} />
          {/* 评测结果路由。 */}
          <Route path="/eval" element={<ModuleAccess module="evaluation"><EvaluationResultPage /></ModuleAccess>} />
          {/* *表示：匹配所有没有被前面 Route 匹配到的路径。整行含义：如果用户访问了不存在的页面，就显示 NotFoundPage。*/}
          <Route path="*" element={<NotFoundPage />} />
        </Route>
      </Routes>
    </BrowserRouter>
  )
}
