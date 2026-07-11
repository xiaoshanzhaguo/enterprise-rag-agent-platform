/*
* 知识库管理页面
*
* 功能描述：
* 1. 管理员在这里查看当前企业知识库和分类配置。
* 2. 页面预留“上传企业文档”入口，后续会接入 /index_document。
* 3. 这个页面是把知识库从聊天 session 中独立出来的前端入口。
*
* 作用：
* 1. 页面加载时，从后端获取企业知识库列表和分类列表。
* 2. 左侧展示“上传企业文档”的表单骨架。
* 3. 右侧展示当前后端返回的知识库配置和分类配置。
* 4. 当前上传功能还没真正接入，只是先搭页面结构。
* */

import { UploadCloud } from 'lucide-react'
// 在这个页面里：useState 保存知识库列表、分类列表、loading、错误信息；useEffect 页面首次加载后请求后端数据。
import { useEffect, useState } from 'react'
import { fetchKnowledgeBaseCategories, fetchKnowledgeBases } from '../api/knowledgeBaseApi'
import type { KnowledgeBaseCategory, KnowledgeBaseItem } from '../types/knowledgeBase'


// KnowledgeAdminPage 是管理员知识库管理页面组件。
export const KnowledgeAdminPage = () => {
  // 保存后端返回的企业知识库列表。
  const [knowledgeBases, setKnowledgeBases] = useState<KnowledgeBaseItem[]>([])

  // 保存后端返回的分类列表。
  const [categories, setCategories] = useState<KnowledgeBaseCategory[]>([])

  // 保存选择的分类ID。
  const [selectedCategoryId, setSelectedCategoryId] = useState<string>('')

  // 标记页面是否正在加载配置。
  // 初始值是：true，因为页面刚打开时就要请求知识库和分类配置。当请求完成后，会执行 setIsLoading(false)。页面里的 <select disabled={isLoading}> 会根据它禁用或启用。
  const [isLoading, setIsLoading] = useState(true)

  // 保存接口失败时的错误提示。
  const [errorMessage, setErrorMessage] = useState('')

  // 页面首次渲染后加载知识库和分类配置。
  // 因为这个 useEffect 最后依赖数组是 []，所以它只会在组件首次挂载后执行一次。
  useEffect(() => {
    // ignore 防止组件卸载后继续 setState。
    // 说明下面的 ignore 变量用于避免异步请求回来后组件已经卸载，但代码还继续 setState。
    // 比如用户很快切换页面，请求还没回来。
    // 如果组件已经卸载了，请求回来后再执行：
    // setKnowledgeBases(...) 就没有意义，还可能引发 React 警告。
    // ignore 初始值为 false，表示组件当前还没有卸载。组件卸载时，清理函数会把它改成 true。
    let ignore = false

    // 异步读取知识库管理页需要的基础配置。
    async function loadKnowledgeBaseConfig() {
      try {
        // 进入加载状态。
        setIsLoading(true)

        // 清空旧错误。
        setErrorMessage('')

        // 知识库列表和分类列表互不依赖，可以并行请求。
        const [knowledgeBaseResponse, categoryResponse] = await Promise.all([
          fetchKnowledgeBases(),
          fetchKnowledgeBaseCategories(),
        ])

        // 如果组件已经卸载，就不再更新状态。
        if (ignore) {
          return
        }

        // 写入知识库列表。
        setKnowledgeBases(knowledgeBaseResponse.knowledge_bases)

        // 写入分类列表。
        setCategories(categoryResponse.categories)

        // 写入选中分类ID，接口返回后默认选中分类
        setSelectedCategoryId(categoryResponse.categories[0]?.category_id ?? '')
      } catch (error) {
        // 如果 try 里的请求失败，就进入 catch。error是捕获到的错误对象。
        // 展示接口错误，方便开发阶段定位后端是否启动。
        setErrorMessage(error instanceof Error ? error.message : '加载知识库管理数据失败')
      } finally {
        // 组件还存在时才结束 loading。
        if (!ignore) {
          setIsLoading(false)
        }
      }
    }

    // 进入页面后立即加载配置。
    loadKnowledgeBaseConfig()

    // 组件卸载时设置 ignore，避免异步请求回来后更新状态。
    return () => {
      ignore = true
    }
  }, [])

  // 返回管理员知识库管理页面结构。
  return (
    // page-stack 控制页面内模块垂直间距。
    // 定义页面最外层区域。
    <section className="page-stack">
      {/* 页面标题区，说明当前入口属于管理员侧。 */}
      {/* 定义页面头部区域。 */}
      <header className="page-header">
        <div>
          <p className="eyebrow">管理员侧</p>
          <h2>知识库管理</h2>
          <p>这里保留管理员上传入口，让知识库从普通聊天 session 中独立出来。</p>
        </div>
      </header>

      {/* 加载失败时展示错误信息。 */}
      {errorMessage ? <div className="alert error">{errorMessage}</div> : null}

      {/* 两列布局：左侧上传入口，右侧当前配置。 */}
      <div className="workspace-grid">
        {/* 左侧：上传企业文档的表单骨架。 */}
        {/* 定义左侧卡片区域。panel 是通用面板样式。 */}
        <section className="panel">
          {/* 定义面板标题区域。 */}
          <div className="panel-heading">
            <UploadCloud aria-hidden="true" size={20} />
            <div>
              <h3>上传企业文档</h3>
              <p>Day 8 先完成页面结构，真正上传接口复用后端 /index_document。</p>
            </div>
          </div>

          {/* 目标知识库：决定文档上传后属于哪个企业知识库。 */}
          <label className="field">
            <span>目标知识库</span>
            {/* disabled={isLoading} 表示：isLoading 为 true 时，下拉框禁用；isLoading 为 false 时，下拉框可用。*/}
            <select disabled={isLoading}>
              {knowledgeBases.map((item) => (
                <option key={item.knowledge_base_id} value={item.knowledge_base_id}>
                  {item.name}
                </option>
              ))}
            </select>
          </label>

          {/* 文档分类：决定上传文档写入 hr、finance、it 还是 product。 */}
          <label className="field">
            <span>文档分类</span>
            <select disabled={isLoading} value={selectedCategoryId} onChange={(event) => setSelectedCategoryId(event.target.value)}>
              {categories.map((item) => (
                <option key={item.category_id} value={item.category_id}>
                  {item.label}
                </option>
              ))}
            </select>
          </label>

          {/* 文件选择：后续接入上传接口时再启用。 */}
          <label className="field">
            <span>选择文件</span>
            {/* 定义文件选择输入框。type="file" 表示这是上传文件用的输入框。disabled 表示当前禁用。
             在 JSX 里单独写 disabled 等价于 disabled={true}。
             当前禁用是因为还没接 /index_document 上传接口。 */}
            <input type="file" disabled />
          </label>

          {/* 上传按钮：后续接入 /index_document 后再取消 disabled。 */}
          {/* type="button" 表示普通按钮，不触发表单默认提交。 */}
          <button className="primary-button" type="button" disabled>
            上传并索引
          </button>
        </section>

        {/* 右侧：展示当前后端提供的知识库和分类配置。 */}
        <aside className="panel">
          {/* 定义右侧面板标题区域。 */}
          <div className="panel-heading">
            {/* small-icon 是 CSS 类名，用于把这个文字显示成小图标样式。 */}
            <div className="small-icon">KB</div>
            <div>
              <h3>当前配置</h3>
              <p>来自 FastAPI 的知识库和分类接口。</p>
            </div>
          </div>

          {/* 知识库配置列表。 */}
          {/* 定义配置列表容器。 */}
          <div className="config-list">
            {/* 遍历 knowledgeBases，每一个知识库渲染一行配置。 */}
            {knowledgeBases.map((item) => (
              <div className="config-row" key={item.knowledge_base_id}>
                {/* {item.name} 表示把变量插入 JSX。 */}
                <strong>{item.name}</strong>
                {/* 显示知识库描述。优先显示知识库描述，没有描述时显示知识库 ID。 */}
                <span>{item.description ?? item.knowledge_base_id}</span>
              </div>
            ))}
          </div>

          {/* 分类标签列表，用于快速确认后端分类接口是否正常。 */}
          {/* 定义分类标签列表容器。 */}
          <div className="tag-list">
            {/* 遍历分类数组。每一个分类渲染一个标签。 */}
            {categories.map((item) => (
              <span className="tag" key={item.category_id}>
                {item.label}
              </span>
            ))}
          </div>
        </aside>
      </div>
    </section>
  )
}
