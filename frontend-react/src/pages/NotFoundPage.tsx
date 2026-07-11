/*
 * 404 页面，为兜底页面
 * 功能描述：
 * 1. 当用户访问不存在的前端路由时显示这个页面。
 * 2. 当前 React 第一版只开放企业问答和知识库管理两个入口。
 * 3. 提供返回企业问答页的按钮，避免用户卡在错误路径。
 */

// Link 是 React Router 提供的前端路由跳转组件。它的作用类似 HTML 里的 a 标签
// 但是在 React 单页应用里，更推荐 Link 组件，因为 Link 跳转时不会刷新整个页面，而是在前端内部切换路由。
import { Link } from 'react-router-dom'

// NotFoundPage 是路由没有匹配上时显示的页面。
// 比如路由配置里：<Route path="*" element={<NotFoundPage />} />，这里的 * 表示匹配所有未被前面路由匹配的路径。
export function NotFoundPage() {
  // 返回一个简单的空状态页面。
  // 空状态页面一般指：页面没有正常内容时显示的提示页面。比如：页面不存在、暂无数据、没有搜索结果、没有权限访问。
  return (
    // empty-state 控制兜底页面居中和间距。
    // empty-state 一般用于控制空状态页面样式，比如：居中显示、上下间距、文字颜色、按钮位置。
    <section className="empty-state">
      {/* 页面主标题。 */}
      <h2>页面不存在</h2>

      {/* 简短说明当前前端第一版的功能范围。 */}
      <p>当前 React 第一版只开放企业问答和知识库管理两个核心入口。</p>

      {/* 返回核心问答入口。 */}
      {/* 定义一个前端路由跳转链接，它最终看起来像一个按钮。
      className="primary-button" 表示这个 Link 使用主按钮样式。虽然它本质是一个链接，但通过 CSS 可以让它看起来像按钮。 */}
      <Link className="primary-button" to="/chat">
        返回企业问答
      </Link>
    </section>
  )
}
