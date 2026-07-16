/*
* 知识库管理页面
*
* 功能描述：
* 1. 管理员在这里查看当前企业知识库和分类配置。
* 2. 页面已经接入“上传企业文档”入口，上传时复用后端 /index_document。
* 3. 这个页面是把知识库从聊天 session 中独立出来的前端入口。
*
* 作用：
* 1. 页面加载时，从后端获取企业知识库列表和分类列表。
* 2. 左侧可以选择目标知识库、文档分类和文本型文件，并提交索引。
* 3. 右侧展示当前后端返回的知识库配置、分类配置和文档状态。
* 4. 上传成功后会刷新当前知识库的文档数、chunk 数和文件列表。
* */

import { FileText, RefreshCw, UploadCloud } from 'lucide-react'
// 在这个页面里：useState 保存知识库列表、分类列表、loading、错误信息；useEffect 页面首次加载后请求后端数据。
import { useEffect, useState } from 'react'
// 只从 React 里导入 ChangeEvent 这个“类型”，用于 TypeScript 类型标注，不会作为运行时代码打包。
// 只导入 React 事件类型，用于给文件选择 input 的 onChange 事件标注类型。
import type { ChangeEvent } from 'react'
import { fetchKnowledgeBaseCategories, fetchKnowledgeBases, fetchRagStatus, indexKnowledgeBaseDocument } from '../api/knowledgeBaseApi'
import type { IndexDocumentResponse, KnowledgeBaseCategory, KnowledgeBaseItem, RagStatusResponse } from '../types/knowledgeBase'


// 管理员上传文档时使用的固定 session_id。
// 说明：真正的知识库范围由 knowledge_base_id 决定，这里保留 session_id 是为了兼容后端 /index_document 的请求模型。
const ADMIN_UPLOAD_SESSION_ID = 'react-admin-knowledge-upload'

// 当前项目还没有正式登录系统，所以管理页先固定使用 kb_admin 角色调用上传接口。
const ADMIN_USER_ROLE = 'kb_admin'

// KnowledgeAdminPage 是管理员知识库管理页面组件。
export const KnowledgeAdminPage = () => {
  // 保存后端返回的企业知识库列表。
  const [knowledgeBases, setKnowledgeBases] = useState<KnowledgeBaseItem[]>([])

  // 保存后端返回的分类列表。
  const [categories, setCategories] = useState<KnowledgeBaseCategory[]>([])

  // 保存选择的分类ID。
  const [selectedCategoryId, setSelectedCategoryId] = useState<string>('')

  // 保存当前选择的目标知识库 ID。
  const [selectedKnowledgeBaseId, setSelectedKnowledgeBaseId] = useState<string>('')

  // 保存当前文件输入框里选择的文件对象。
  const [selectedFile, setSelectedFile] = useState<File | null>(null)

  // 保存当前知识库的文档状态，例如文档数、chunk 数和文件列表。
  const [ragStatus, setRagStatus] = useState<RagStatusResponse | null>(null)

  // 保存最近一次上传成功后的后端响应，方便管理员确认 chunk 数和分类。
  const [uploadResult, setUploadResult] = useState<IndexDocumentResponse | null>(null)

  // 标记页面是否正在加载配置。
  // 初始值是：true，因为页面刚打开时就要请求知识库和分类配置。当请求完成后，会执行 setIsLoading(false)。页面里的 <select disabled={isLoading}> 会根据它禁用或启用。
  const [isLoading, setIsLoading] = useState(true)

  // 标记当前是否正在上传并索引文档。
  const [isUploading, setIsUploading] = useState(false)

  // 标记当前是否正在刷新右侧文档状态。
  const [isStatusLoading, setIsStatusLoading] = useState(false)

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

        // 写入默认知识库 ID，接口返回后默认选中第一个知识库。
        setSelectedKnowledgeBaseId(knowledgeBaseResponse.knowledge_bases[0]?.knowledge_base_id ?? '')

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

  // 当目标知识库变化时，自动刷新这个知识库的文档状态。
  useEffect(() => {
    // 没有选中知识库时，清空右侧文档状态。
    // 这段的作用：如果当前没有选中知识库，就不要请求文档状态；同时把右侧 ragStatus 清空，避免继续显示旧知识库的文档状态。
    // 它主要防的是这几种状态：
    // 1. 页面刚打开时，selectedKnowledgeBaseId 还是 ''
    // 2. 后端返回的 knowledge_bases 是空数组，默认值仍然是 ''
    // 3. 将来如果你加了“请清空知识库”的空选项，用户可能手动选回‘’
    if (!selectedKnowledgeBaseId) {
      // 清空状态，避免页面显示上一个知识库的旧文档。
      // 状态清空已移动到 handleKnowledgeBaseChange，避免在 effect 中同步 setState。
      // 直接结束本次 effect。
      return
    }

    // ignore 用来避免组件卸载后继续 setState。
    // ignore 为 false 表示组件还未卸载。
    let ignore = false

    // 异步加载当前选中知识库的文档状态。
    async function loadStatus() {
      try {
        // 进入文档状态加载中。
        setIsStatusLoading(true)
        // 调用后端 RAG 状态接口。
        const response = await fetchRagStatus(ADMIN_UPLOAD_SESSION_ID, selectedKnowledgeBaseId)

        // 如果组件还存在，就写入状态。
        if (!ignore) {
          // 保存文档状态，右侧面板会展示文档数、chunk 数和文件列表。
          setRagStatus(response)
        }
      } catch (error) {
        // 如果请求失败，展示错误信息。
        if (!ignore) {
          // 尽量展示真实错误；拿不到时展示兜底文案。
          setErrorMessage(error instanceof Error ? error.message : '加载文档状态失败')
          // 清空文档状态，避免旧数据误导用户。
          setRagStatus(null)
        }
      } finally {
        // 组件还存在时才结束加载。
        if (!ignore) {
          // 结束文档状态加载。
          setIsStatusLoading(false)
        }
      }
    }

    // 立即加载当前知识库状态。
    loadStatus()

    // 清理函数：组件卸载或 selectedKnowledgeBaseId 改变时执行。
    return () => {
      // 标记旧请求不再允许更新状态。
      // 标记当前这次 effect 已失效，旧请求回来后不允许再更新状态。
      ignore = true
    }
  }, [selectedKnowledgeBaseId])

  // 根据 selectedCategoryId 找到当前选中的分类对象。
  const selectedCategory = categories.find((item) => item.category_id === selectedCategoryId)

  // 根据 selectedKnowledgeBaseId 找到当前选中的知识库对象。
  const selectedKnowledgeBase = knowledgeBases.find((item) => item.knowledge_base_id === selectedKnowledgeBaseId)

  // 切换目标知识库时，先清空旧状态，再保存新的知识库 ID。
  // 这样右侧不会短暂展示上一个知识库的数据，也能避免在 useEffect 中同步调用 setState。
  function handleKnowledgeBaseChange(event: ChangeEvent<HTMLSelectElement>) {
    // 读取下拉框当前选中的知识库 ID。
    const nextKnowledgeBaseId = event.target.value
    // 清空旧知识库状态，等待后续 useEffect 请求新知识库数据。
    setRagStatus(null)
    // 清空上一轮上传结果，避免它和新知识库范围混在一起展示。
    setUploadResult(null)
    // 保存新知识库 ID，触发上面的状态加载 effect。
    setSelectedKnowledgeBaseId(nextKnowledgeBaseId)
  }

  // 主动刷新当前选中知识库的文档状态。
  // 使用场景：点击“刷新”按钮，或上传文档成功后重新拉取最新文档数、chunk 数和文件列表。
  // 注意：useEffect 只会在 selectedKnowledgeBaseId 变化时自动刷新；
  // 如果知识库 ID 没变，但后端文档数据变了，就需要手动调用这个函数。
  async function refreshSelectedKnowledgeBaseStatus() {
    // 没有选中知识库时不请求后端。
    if (!selectedKnowledgeBaseId) {
      return
    }

    try {
      // 进入文档状态加载中。
      setIsStatusLoading(true)
      // 清空旧错误。
      setErrorMessage('')
      // 调用状态接口，按企业知识库范围查询。
      const response = await fetchRagStatus(ADMIN_UPLOAD_SESSION_ID, selectedKnowledgeBaseId)
      // 写入最新状态。
      setRagStatus(response)
    } catch (error) {
      // 展示接口错误。
      setErrorMessage(error instanceof Error ? error.message : '刷新文档状态失败')
    } finally {
      // 结束文档状态加载。
      setIsStatusLoading(false)
    }
  }

  // 文件选择变化时，把浏览器 File 对象保存到状态里。
  // ChangeEvent<HTMLInputElement>：这是一个 React 的 change 事件，并且这个事件来自一个 HTML input 元素。
  // ChangeEvent 是 React 提供的一个事件类型，它表示：表单元素发生变化时触发的事件类型。
  // event 是 input 文件选择框触发的 change 事件，因此可以读取 event.target.files。
  function handleFileChange(event: ChangeEvent<HTMLInputElement>) {
    // 读取用户选择的第一个文件。
    const file = event.target.files?.[0] ?? null

    if (!file) {
      setSelectedFile(null)
      return
    }

    const extension = file.name.slice(file.name.lastIndexOf(".")).toLowerCase()
    const allowedExtensions = [".md", ".txt"]

    // allowedExtensions.includes(extension): 判断“允许列表里是否包含当前文件扩展名”。
    if (!allowedExtensions.includes(extension)) {
      setSelectedFile(null)
      setErrorMessage('只允许选择 .md 和 .txt 文件，类型不合适，请重新选择。')
      event.target.value = ''
      return
    }

    setErrorMessage('')
    // 保存文件对象，后续点击上传时读取文本内容。
    setSelectedFile(file)
    // 重新选择文件后，清空上一轮上传结果。
    setUploadResult(null)
  }

  // 点击“上传并索引”后，读取文件文本并调用后端 /index_document。
  async function handleUploadDocument() {
    // 没有选中文件时，不允许上传。
    if (!selectedFile) {
      // 给出明确提示。
      setErrorMessage('请先选择要上传的文档。')
      // 直接结束函数。
      return
    }

    // 没有选中知识库时，不允许上传。
    if (!selectedKnowledgeBaseId) {
      // 给出明确提示。
      setErrorMessage('请先选择目标知识库。')
      // 直接结束函数。
      return
    }

    // 没有选中文档分类时，不允许上传。
    if (!selectedCategory) {
      // 给出明确提示。
      setErrorMessage('请先选择文档分类。')
      // 直接结束函数。
      return
    }

    // 限制上传文件大小，避免一次性读取过大的文本文件。
    const MAX_FILE_SIZE = 2 * 1024 * 1024

    if (selectedFile.size > MAX_FILE_SIZE) {
      setErrorMessage('文件过大，请上传 2MB 以内的文本文件。')
      return
    }

    try {
      // 进入上传中状态。
      setIsUploading(true)
      // 清空旧错误。
      setErrorMessage('')
      // 读取文件文本内容。当前后端接收 document_text，因此这里先支持 md、txt 等文本型文件。
      // selectedFile 是浏览器里的 File 对象。selectedFile.text() 的作用是：把用户选择的文件内容读取成字符串。
      // 为什么它是异步的？因为读取文件属于浏览器的 I/O 操作。浏览器不会为了读一个文件，把整个页面卡住，所以设计成异步操作，返回一个 Promise。
      const documentText = await selectedFile.text()

      // 调用后端 /index_document，把文件内容写入企业知识库。
      const response = await indexKnowledgeBaseDocument({
        session_id: ADMIN_UPLOAD_SESSION_ID, // 兼容后端请求模型。
        document_text: documentText, // 文件正文。
        file_name: selectedFile.name, // 文件名。
        knowledge_base_id: selectedKnowledgeBaseId, // 目标知识库。
        user_role: ADMIN_USER_ROLE, // 当前管理页固定模拟知识库管理员。
        knowledge_base_type: selectedCategory.knowledge_base_type, // 文档分类。
        department: selectedCategory.department, // 分类对应部门。
        process_status: 'active', // 当前上传文档默认启用。
      })

      // 保存上传结果，给管理员展示 chunk 数和最终分类。
      setUploadResult(response)
      // 上传成功后刷新右侧文档状态，让新文件立即出现在列表里。
      // 等右侧知识库状态刷新完成后，再继续往下走。
      await refreshSelectedKnowledgeBaseStatus()
    } catch (error) {
      // 展示上传失败原因。
      setErrorMessage(error instanceof Error ? error.message : '上传并索引文档失败')
    } finally {
      // 结束上传中状态。
      setIsUploading(false)
    }
  }

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
              <p>复用后端 /index_document，把文本型文档写入选中的企业知识库。</p>
            </div>
          </div>

          {/* 目标知识库：决定文档上传后属于哪个企业知识库。 */}
          <label className="field">
            <span>目标知识库</span>
            {/* disabled={isLoading} 表示：isLoading 为 true 时，下拉框禁用；isLoading 为 false 时，下拉框可用。*/}
            <select disabled={isLoading || isUploading} value={selectedKnowledgeBaseId} onChange={handleKnowledgeBaseChange}>
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
            <select disabled={isLoading || isUploading} value={selectedCategoryId} onChange={(event) => setSelectedCategoryId(event.target.value)}>
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
            {/* 定义文件选择输入框。type="file" 表示这是上传文件用的输入框。 */}
            {/* 当前已经接入 /index_document，但后端接收 document_text，所以这里先支持文本型文件。 */}
            {/* 当前后端接收 document_text，所以前端先读取文本型文件内容，例如 .md、.txt、.csv、.json。 */}
            <input type="file" accept=".md,.txt,.csv,.json" disabled={isLoading || isUploading} onChange={handleFileChange} />
          </label>

          {/* 上传按钮：点击后读取文件文本，并提交给 /index_document。 */}
          {/* type="button" 表示普通按钮，不触发表单默认提交。 */}
          <button className="primary-button" type="button" disabled={!selectedFile || !selectedKnowledgeBaseId || !selectedCategoryId || isUploading} onClick={handleUploadDocument}>
            {isUploading ? '上传中...' : '上传并索引'}
          </button>

          {/* 上传成功后展示后端返回的索引结果。 */}
          {uploadResult ? (
            <div className="upload-result-card">
              {/* 上传结果标题。 */}
              <strong>索引完成</strong>
              {/* 文件名。 */}
              <span>文件：{uploadResult.file_name ?? selectedFile?.name ?? '未命名文档'}</span>
              {/* chunk 数。 */}
              <span>Chunk 数：{uploadResult.chunk_count}</span>
              {/* 文档分类。 */}
              <span>分类：{uploadResult.knowledge_base_type}</span>
            </div>
          ) : null}
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

          {/* 当前选中知识库的文档状态。 */}
          <div className="admin-status-header">
            {/* 左侧展示当前知识库名称。 */}
            <div>
              <strong>{selectedKnowledgeBase?.name ?? '未选择知识库'}</strong>
              <span>{isStatusLoading ? '正在刷新文档状态...' : '文档状态'}</span>
            </div>

            {/* 手动刷新文档状态按钮。 */}
            <button className="ghost-button" type="button" disabled={!selectedKnowledgeBaseId || isStatusLoading} onClick={refreshSelectedKnowledgeBaseStatus}>
              <RefreshCw aria-hidden="true" size={16} />
              刷新
            </button>
          </div>

          {/* 文档状态数字摘要。 */}
          <dl className="meta-list">
            {/* 文档总数。 */}
            <div>
              <dt>文档数</dt>
              <dd>{ragStatus?.document_count ?? 0}</dd>
            </div>
            {/* Chunk 总数。 */}
            <div>
              <dt>Chunk 数</dt>
              <dd>{ragStatus?.chunk_count ?? 0}</dd>
            </div>
          </dl>

          {/* 当前知识库中的文档列表。 */}
          <div className="file-list">
            {/* 遍历后端返回的 documents。 */}
            {(ragStatus?.documents ?? []).map((document) => (
              // 单个文档状态行。
              <div className="file-row" key={`${document.document_id}-${document.file_name}`}>
                <FileText aria-hidden="true" size={16} />
                <span>{document.file_name ?? '未命名文档'}</span>
                <small>{document.knowledge_base_type ?? 'general'}</small>
              </div>
            ))}

            {/* 没有文档时展示空状态。 */}
            {/* 如果当前知识库没有文档，就显示一句“当前知识库还没有已索引文档。”；如果有文档，就什么都不显示。 */}
            {/* ragStatus?.documents?.length: 安全地读取 ragStatus 里面 documents 数组的长度。
            !ragStatus?.documents?.length: 如果文档数量不存在，或者文档数量是 0，就认为没有文档。*/}
            {!ragStatus?.documents?.length ? <p className="muted-text">当前知识库还没有已索引文档。</p> : null}
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
