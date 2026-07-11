// 根组件，负责写页面主体内容

import { AppRouter } from './routes/AppRouter'

/*
 * 功能描述：
 * 1. App 是 React 应用的最外层组件。
 * 2. 当前不直接写页面内容，而是交给 AppRouter 根据路由决定显示哪个页面。
 * 3. 这样后续新增页面时，只需要扩展路由，不需要改 main.tsx。
 */

// App 是 React 应用的根组件。
function App() {
  // 渲染路由系统，让 /chat、/admin/knowledge 等路径生效。
  return <AppRouter />
}

// 默认导出 App，main.tsx 会导入它并挂载到页面上。
export default App
