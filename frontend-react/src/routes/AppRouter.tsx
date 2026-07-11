// BrowserRouter: 表示启用浏览器路由，它负责监听浏览器地址栏，例如 /chat，然后根据地址栏路径决定显示哪个页面。
// Routes: 表示一组路由规则的容器，可以理解为：Routes 里面放很多 Route。
// Route: 表示一条具体路由规则。比如：<Route path=/chat" element={<ChatPage />} />，意思是：当地址栏是 /chat 时，显示 ChatPage 页面。
// Navigate: 表示重定向，也就是自动跳转。比如：<Navigate to="/chat" replace />，意思是：自动跳转到 /chat
// replace 的意思是：跳转到 /chat 时，替换当前这条浏览器历史记录，而不是新增一条历史记录。

import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom'
import { MainLayout } from '../layouts/MainLayout'
import { ChatPage } from '../pages/ChatPage'
// 评测结果页面。
import { EvaluationResultPage } from '../pages/EvaluationResultPage'
// 执行日志页面。
import { ExecutionLogPage } from '../pages/ExecutionLogPage'
import { KnowledgeAdminPage } from '../pages/KnowledgeAdminPage'
import { NotFoundPage } from '../pages/NotFoundPage'
// 流程助手页面。
import { ProcessAssistantPage } from '../pages/ProcessAssistantPage'

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
        <Route element={<MainLayout />}>
          {/*默认子路由：访问 / 时跳转到 /chat。index 表示默认页面，因为它写在内部，因此它表示 MainLayout 下的默认子页面。*/}
          {/*整行理解：用户访问首页 / 时，自动跳转到 /chat，并且不要在浏览器历史里保留 /。*/}
          <Route index element={<Navigate to="/chat" replace />} />
          <Route path="/chat" element={<ChatPage />} />
          <Route path="/admin/knowledge" element={<KnowledgeAdminPage />} />
          {/* 流程助手路由。 */}
          <Route path="/workflow" element={<ProcessAssistantPage />} />
          {/* 执行日志路由。 */}
          <Route path="/logs" element={<ExecutionLogPage />} />
          {/* 评测结果路由。 */}
          <Route path="/eval" element={<EvaluationResultPage />} />
          {/* *表示：匹配所有没有被前面 Route 匹配到的路径。整行含义：如果用户访问了不存在的页面，就显示 NotFoundPage。*/}
          <Route path="*" element={<NotFoundPage />} />
        </Route>
      </Routes>
    </BrowserRouter>
  )
}
