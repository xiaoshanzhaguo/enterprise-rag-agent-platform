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
import { formatApiError } from '../api/httpClient'
// 在这个页面里：useState 保存知识库列表、分类列表、loading、错误信息；useEffect 页面首次加载后请求后端数据。
import { useEffect, useMemo, useRef, useState, useSyncExternalStore } from 'react'
// 只从 React 里导入 ChangeEvent 这个“类型”，用于 TypeScript 类型标注，不会作为运行时代码打包。
// 只导入 React 事件类型，用于给文件选择 input 的 onChange 事件标注类型。
import type { ChangeEvent } from 'react'
import * as knowledgeBaseApi from '../api/knowledgeBaseApi'
import { KnowledgeIndexController } from '../api/knowledgeIndexState'
import { DocumentIndexStatus } from '../components/DocumentIndexStatus'
import type { KnowledgeBaseCategory, KnowledgeBaseItem } from '../types/knowledgeBase'


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

  // 标记页面是否正在加载配置。
  // 初始值是：true，因为页面刚打开时就要请求知识库和分类配置。当请求完成后，会执行 setIsLoading(false)。页面里的 <select disabled={isLoading}> 会根据它禁用或启用。
  const [isLoading, setIsLoading] = useState(true)

  // 保存接口失败时的错误提示。
  const [errorMessage, setErrorMessage] = useState('')
  const [isReadingFile, setIsReadingFile] = useState(false)
  const readingFile = useRef(false)
  const indexController = useMemo(() => new KnowledgeIndexController({
    session_id: ADMIN_UPLOAD_SESSION_ID, knowledge_base_id: selectedKnowledgeBaseId, user_role: ADMIN_USER_ROLE,
  }, knowledgeBaseApi), [selectedKnowledgeBaseId])
  const indexState = useSyncExternalStore(indexController.subscribe, indexController.getSnapshot)
  const ragStatus = indexState.status
  const uploadResult = indexState.upload
  const isUploading = indexState.uploading || isReadingFile
  const isStatusLoading = indexState.loading
  const visibleError = errorMessage || indexState.error || indexState.queryError


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
          knowledgeBaseApi.fetchKnowledgeBases(),
          knowledgeBaseApi.fetchKnowledgeBaseCategories(),
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
        setErrorMessage(formatApiError(error, '加载知识库管理数据失败，请检查服务连接后刷新页面。'))
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

  // 切换知识库或离开页面时取消 GET 和轮询；不重新提交索引任务。
  useEffect(() => {
    indexController.start()
    return () => indexController.dispose()
  }, [indexController])

  // 根据 selectedCategoryId 找到当前选中的分类对象。
  const selectedCategory = categories.find((item) => item.category_id === selectedCategoryId)

  // 根据 selectedKnowledgeBaseId 找到当前选中的知识库对象。
  const selectedKnowledgeBase = knowledgeBases.find((item) => item.knowledge_base_id === selectedKnowledgeBaseId)

  function handleKnowledgeBaseChange(event: ChangeEvent<HTMLSelectElement>) {
    setErrorMessage('')
    setSelectedKnowledgeBaseId(event.target.value)
  }

  function refreshSelectedKnowledgeBaseStatus() {
    setErrorMessage('')
    void indexController.refresh()
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
    indexController.clearUpload()
  }

  // 点击“上传并索引”后，读取文件文本并调用后端 /index_document。
  async function handleUploadDocument() {
    if (readingFile.current || isUploading || indexState.uploadUncertain) return
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
      readingFile.current = true
      setIsReadingFile(true)
      // 清空旧错误。
      setErrorMessage('')
      // 读取文件文本内容。当前后端接收 document_text，因此这里先支持 md、txt 等文本型文件。
      // selectedFile 是浏览器里的 File 对象。selectedFile.text() 的作用是：把用户选择的文件内容读取成字符串。
      // 为什么它是异步的？因为读取文件属于浏览器的 I/O 操作。浏览器不会为了读一个文件，把整个页面卡住，所以设计成异步操作，返回一个 Promise。
      const documentText = await selectedFile.text()

      // 调用后端 /index_document，把文件内容写入企业知识库。
      await indexController.upload({
        session_id: ADMIN_UPLOAD_SESSION_ID, // 兼容后端请求模型。
        document_text: documentText, // 文件正文。
        file_name: selectedFile.name, // 文件名。
        knowledge_base_id: selectedKnowledgeBaseId, // 目标知识库。
        user_role: ADMIN_USER_ROLE, // 当前管理页固定模拟知识库管理员。
        knowledge_base_type: selectedCategory.knowledge_base_type, // 文档分类。
        department: selectedCategory.department, // 分类对应部门。
        process_status: 'active', // 当前上传文档默认启用。
      })

    } catch (error) {
      // 展示上传失败原因。
      setErrorMessage(formatApiError(error, '读取文档失败，请重新选择文件。'))
    } finally {
      // 结束上传中状态。
      readingFile.current = false
      setIsReadingFile(false)
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
          <p>集中维护企业资料，按业务分类整理，为员工提供可信的知识来源。</p>
        </div>
      </header>

      {/* 加载失败时展示错误信息。 */}
      {visibleError ? <div className="alert error" role="alert">{visibleError}</div> : null}

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
              <p>选择分类并上传文本资料，处理完成后即可用于问答检索。</p>
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
            <input type="file" accept=".md,.txt" disabled={isLoading || isUploading} onChange={handleFileChange} />
          </label>

          {/* 上传按钮：点击后读取文件文本，并提交给 /index_document。 */}
          {/* type="button" 表示普通按钮，不触发表单默认提交。 */}
          <button className="primary-button" type="button" disabled={!selectedFile || !selectedKnowledgeBaseId || !selectedCategoryId || isUploading || indexState.uploadUncertain} onClick={handleUploadDocument}>
            {isUploading ? '正在保存并处理…' : indexState.uploadUncertain ? '请先刷新核对上传结果' : '上传并索引'}
          </button>

          {/* 上传成功后展示后端返回的索引结果。 */}
          {uploadResult ? (
            <div className="upload-result-card">
              {/* 上传结果标题。 */}
              <strong>文本已保存</strong>
              <DocumentIndexStatus index={uploadResult.vector_index} operationId={uploadResult.operation_id}
                uncertain={!uploadResult.state_confirmed || indexState.uncertain.includes(uploadResult.operation_id || '')}
                busy={indexState.busy.includes(uploadResult.operation_id || '')}
                onQuery={() => { if (uploadResult.operation_id) void indexController.query(uploadResult.operation_id) }}
                onExecute={action => { if (uploadResult.operation_id) void indexController.execute(uploadResult.operation_id, action) }} />
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
              <h3>知识库概览</h3>
              <p>查看已收录文档及其分类与处理情况。</p>
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
            {(ragStatus?.documents ?? []).map(document => {
              const operationId = document.vector_index?.operation_id
                || indexState.uncertainDocuments[document.document_id ?? 0]
              return <div className="knowledge-document" key={document.document_id ?? document.file_name}>
                <div className="knowledge-document-title">
                  <FileText aria-hidden="true" size={17} />
                  <strong title={document.file_name ?? '未命名文档'}>{document.file_name ?? '未命名文档'}</strong>
                  <small>{document.knowledge_base_type ?? 'general'} · {document.chunk_count ?? 0} 个片段</small>
                </div>
                <DocumentIndexStatus index={document.vector_index} operationId={operationId}
                  uncertain={Boolean(operationId && indexState.uncertain.includes(operationId))}
                  busy={Boolean(operationId && indexState.busy.includes(operationId))}
                  onQuery={operationId ? () => { void indexController.query(operationId) } : undefined}
                  onExecute={operationId ? action => { void indexController.execute(operationId, action) } : undefined} />
              </div>
            })}

            {/* 没有文档时展示空状态。 */}
            {/* 如果当前知识库没有文档，就显示一句“当前知识库还没有文档。”；如果有文档，就什么都不显示。 */}
            {/* ragStatus?.documents?.length: 安全地读取 ragStatus 里面 documents 数组的长度。
            !ragStatus?.documents?.length: 如果文档数量不存在，或者文档数量是 0，就认为没有文档。*/}
            {!ragStatus?.documents?.length ? <p className="muted-text">当前知识库还没有文档。</p> : null}
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
