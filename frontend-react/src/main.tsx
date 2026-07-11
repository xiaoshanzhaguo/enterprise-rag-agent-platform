// React入口文件，负责把 App 挂载到 root上
// 核心含义：找到 index.html 里的 root 节点，然后把 App 组件渲染进去

// StrictMode 是 React 的严格模式，主要用于开发阶段帮助你发现潜在的问题
import { StrictMode } from 'react'
// createRoot：创建 React 应用的根节点，也就是告诉 React: 我要把 React 组件渲染到 HTML 的哪个位置。
import { createRoot } from 'react-dom/client'
import './index.css'
// 从 App.tsx 文件中引入默认导出的 App 组件。
import App from './App.tsx'

// !: TS中的非空断言，意思是告诉TS，我确定这里能找到 root，不会是null
// createRoot: 用这个 HTML 节点创建 React 根节点
createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
