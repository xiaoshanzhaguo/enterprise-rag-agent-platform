/*
 * 核心员工问答页面
 *
 * 功能描述：
 * 1. 员工在这里选择企业知识库和检索分类。
 * 2. 页面会从后端读取知识库列表、分类列表、当前知识库的 RAG 文档状态。
 * 3. 后续再接入真正的提问和流式回答。
 */

// Bot             机器人图标，用于提问区
// Database        数据库图标，用于文档或知识库
// FileSearch      文件检索图标，用于检索上下文区域
// SendHorizontal  发送图标，用于发送按钮
import { Bot, Database, FileSearch, SendHorizontal } from 'lucide-react'
// useState 保存知识库列表、分类列表、当前选中项、RAG 状态、loading、错误信息。
// useEffect 页面加载时请求接口，或知识库切换时重新请求状态。
// useMemo 根据当前 ID 从列表里找出当前选中的完整对象。
import { useEffect, useMemo, useState } from 'react'
import { fetchKnowledgeBaseCategories, fetchKnowledgeBases, fetchRagStatus } from '../api/knowledgeBaseApi'
import type { KnowledgeBaseCategory, KnowledgeBaseItem, RagStatusResponse } from '../types/knowledgeBase'


// 当前 React 页面只是预览版，还没有真正创建聊天 session。所以先写死一个 session_id，用它去调用 RAG 状态接口。
// 后续真正接入聊天功能后，可能会由后端创建真实会话 ID。
const PREVIEW_SESSION_ID = 'react-preview-session'

// ChatPage 是员工问答页面组件。
export function ChatPage() {
  // 保存后端返回的企业知识库列表。
  const [knowledgeBases, setKnowledgeBases] = useState<KnowledgeBaseItem[]>([])

  // 保存后端返回的知识库分类列表。
  const [categories, setCategories] = useState<KnowledgeBaseCategory[]>([])

  // 保存当前选中的企业知识库 ID。
  const [selectedKnowledgeBaseId, setSelectedKnowledgeBaseId] = useState('')

  // 保存当前选中的分类；all 表示不过滤分类。
  const [selectedCategoryId, setSelectedCategoryId] = useState('all')

  // 保存当前知识库的文档状态，例如文档数、chunk 数、文件列表。
  const [ragStatus, setRagStatus] = useState<RagStatusResponse | null>(null)

  // 保存用户输入的问题
  const [question, setQuestion] = useState('')

  // 页面首次加载接口时展示禁用状态。
  const [isLoading, setIsLoading] = useState(true)

  // 保存接口失败时的错误提示。
  const [errorMessage, setErrorMessage] = useState('')

  // 首次进入页面时加载知识库列表和分类列表。
  // 因为后面依赖数组是 []，所以它只会在页面首次进入时执行一次。
  useEffect(() => {
    // ignore 用于避免组件卸载后还继续 setState，减少 React 警告。
    // 异步请求发出去后，用户可能已经离开页面了。
    // 如果页面已经卸载，请求回来后再执行 setState，可能导致警告或无意义更新。
    // 所以用 ignore 做保护。
    // false 表示当前组件还没有卸载。当组件卸载时，清理函数会把它改成：true
    let ignore = false

    // 异步加载页面初始化需要的配置数据。
    // 定义一个异步函数，因为里面要用：await Promise.all()，所以函数前要加 async。这个函数不会自动执行，后面会手动调用 loadInitialData()
    async function loadInitialData() {
      // 尝试执行接口请求逻辑。如果中途报错，就进入 catch。最后一定执行 finally。
      try {
        // 开始加载前先进入 loading 状态。作用：告诉页面，现在正在请求接口。
        // 后面知识库选择框里有 disabled={isLoading}，所以 loading 期间选择框会被禁用。
        setIsLoading(true)

        // 清空旧错误，避免上一次失败提示残留。
        setErrorMessage('')

        // 知识库列表和分类列表互不依赖，可以并行请求。
        // 两个接口互不依赖，所以并行请求更快。
        // 执行完成后，两个接口都成功返回，才会继续往下执行。如果其中任意一个失败，就会进入 catch。
        const [knowledgeBaseResponse, categoryResponse] = await Promise.all([
          fetchKnowledgeBases(),
          fetchKnowledgeBaseCategories(),
        ])

        // 如果组件已经卸载，就不要再更新状态。
        if (ignore) {
          // 直接结束 loadInitialData 函数。也就是组件已经卸载，后面的 setState 全部不执行。
          return
        }

        // 默认选中第一个知识库，保证页面有一个可用范围。
        // 如果数组为空，firstKnowledgeBase 就是 undefined。
        const firstKnowledgeBase = knowledgeBaseResponse.knowledge_bases[0]

        // 写入知识库列表。
        // 把后端返回的知识库列表保存到 knowledgeBases。
        // 页面会因此重新渲染，知识库下拉框会出现选项。
        setKnowledgeBases(knowledgeBaseResponse.knowledge_bases)

        // 写入分类列表。
        setCategories(categoryResponse.categories)

        // 如果后端返回了知识库，就默认选中第一个；否则保持空字符串。
        // firstKnowledgeBase?.knowledge_base_id表示：如果 firstKnowledgeBase 存在，就取它的 knowledge_base_id；如果 firstKnowledgeBase 不存在，就返回 undefined。
        // ?. 叫可选链，后面的 ?? 表示：如果左边是 null 或 undefined，就使用空字符串。
        // 整句含义：如果有第一个知识库，就默认选中它；如果没有任何知识库，就设置为空字符串。
        setSelectedKnowledgeBaseId(firstKnowledgeBase?.knowledge_base_id ?? '')
      } catch (error) {
        // 如果 try 里的请求失败，就进入 catch。error是捕获到的错误对象。
        // 接口失败时，把错误信息展示到页面上。
        // 接口失败时，尽量展示真实错误信息；拿不到标准错误时，展示兜底错误文案。
        setErrorMessage(error instanceof Error ? error.message : '加载知识库配置失败')
      } finally {
        // finally 表示无论成功还是失败，最后都会执行。这里用于结束 loading。
        // 组件还存在时才结束 loading。
        // 如果 ignore 是 false，说明组件还在页面上，即组件没卸载。
        if (!ignore) {
          // 将 isLoading 设置为 false，表示初始化加载结束。
          setIsLoading(false)
        }
      }
    }

    // 进入页面后立即执行初始化加载。也就是页面首次进入后，马上请求知识库列表、知识库分类列表。
    loadInitialData()

    // 组件卸载时把 ignore 改成 true，避免异步请求回来后继续 setState。
    // 下面的 return 为 useEffect 的清理函数。useEffect 里返回一个函数，表示清理逻辑。当组件卸载时，React 会执行这个函数。
    return () => {
      // 组件卸载时，把 ignore 改成 true。这样异步请求回来后，就不会继续更新状态。
      ignore = true
    }
  }, [])

  // 当用户切换知识库时，重新读取这个知识库下的 RAG 状态。
  // 这个 useEffect 的作用：selectedKnowledgeBaseId 变化时，重新请求 RAG 状态。
  useEffect(() => {
    // 和上面的 effect 一样，用于避免卸载后 setState。
    let ignore = false

    // 加载当前知识库的文档状态。
    // 定义异步函数，用来请求 RAG 状态接口。
    async function loadRagStatus() {
      // 没有选中知识库时，右侧状态直接清空。
      if (!selectedKnowledgeBaseId) {
        // 把 RAG 状态设置为 null，页面右侧会显示：文档数0，Chunk数0，暂无文档状态。
        setRagStatus(null)
        // 直接结束函数，不再请求后端。因为没有知识库 ID，没法查询具体知识库状态。
        return
      }

      // 开始请求 RAG 状态的错误捕获。
      try {
        // 传入 knowledge_base_id 后，后端会按企业知识库范围查询状态。
        const response = await fetchRagStatus(PREVIEW_SESSION_ID, selectedKnowledgeBaseId)

        // 如果组件仍然存在，就写入最新状态。
        if (!ignore) {
          // 把接口返回的 RAG 状态保存到 ragStatus。页面右侧的文档数、chunk 数、文件列表会更新。
          setRagStatus(response)
        }
      } catch {
        // 状态接口失败不阻塞主页面，先清空右侧状态即可。
        // 如果请求 RAG 状态失败，就进入 catch。这里没有写 error，因为当前逻辑不需要具体错误内容。
        // 组件仍然存在时，才更新状态。
        if (!ignore) {
          // 请求失败时，把 RAG 状态清空。
          setRagStatus(null)
        }
      }
    }

    // 选中知识库变化后立即刷新状态。当 selectedKnowledgeBaseId 变化时，这个函数会重新执行。
    loadRagStatus()

    // 清理函数，避免异步请求回来后更新已卸载组件。
    // 组件卸载或 effect 重新执行前，React 会调用这个清理函数。
    return () => {
      // 将 ignore 改成 true，防止旧请求回来后更新状态。
      ignore = true
    }
  }, [selectedKnowledgeBaseId]) // 组件首次渲染后执行一次；以后每次 selectedKnowledgeBaseId 变化时，再执行一次。

  // 根据 selectedKnowledgeBaseId 从列表里找到完整知识库对象。
  // 定义一个缓存计算值。selectedKnowledgeBase 表示当前选中的完整知识库对象。
  // 使用 useMemo 是为了：只有 knowledgeBases 或 selectedKnowledgeBaseId 变化时，才重新查找。
  const selectedKnowledgeBase = useMemo(
    // knowledgeBases.find 表示从知识库列表里找到符合条件的第一项，找到 ID 等于当前选中 ID 的知识库。找到了返回知识库对象，否则返回 undefined。
    () => knowledgeBases.find((item) => item.knowledge_base_id === selectedKnowledgeBaseId),
    [knowledgeBases, selectedKnowledgeBaseId],
  )

  // 根据 selectedCategoryId 从列表里找到完整分类对象。
  // 注意：当 selectedCategoryId 是 'all' 时，分类列表里通常没有 category_id === 'all'，所以结果是 undefined。
  // 后面页面会用：selectedCategory?.label ?? '全部分类' 来处理这种情况。
  const selectedCategory = useMemo(
    () => categories.find((item) => item.category_id === selectedCategoryId),
    [categories, selectedCategoryId],
  )

  // 返回页面结构。说明下面进入 JSX 页面结构。
  // return ( 表示 ChatPage 组件要返回页面 JSX。
  return (
    // page-stack 控制页面上下模块间距。
    // 页面最外层容器。section 是语义化标签，表示页面中的一个区域。
    <section className="page-stack">
      {/* 页面标题区，说明当前页面属于员工侧问答入口。 */}
      {/* 页面头部区域。 */}
      <header className="page-header">
        <div>
          <p className="eyebrow">员工侧</p>
          <h2>企业知识问答</h2>
          <p>第一版 React 先承接核心闭环：选择知识库、限定分类、向后端发起 Agent + RAG 问答。</p>
        </div>
      </header>

      {/* 如果接口加载失败，就在页面顶部展示错误。 */}
      {/* 这是 JSX 条件渲染。逻辑是：如果 errorMessage 有内容，就显示错误框；如果 errorMessage 是空字符串，就什么都不显示。  */}
      {errorMessage ? <div className="alert error">{errorMessage}</div> : null}

      {/* workspace-grid 把左侧提问区和右侧检索上下文分成两列。 */}
      {/* 定义一个布局容器。workspace-grid 通常用 CSS Grid 把页面分成两列。 */}
      <div className="workspace-grid">
        {/* 左侧：员工提问和检索范围选择。 */}
        {/* 定义左侧面板，panel 是公共卡片样式。 */}
        <section className="panel">
          {/* 定义面板标题区域。 */}
          <div className="panel-heading">
            {/* 显示机器人图标。 */}
            <Bot aria-hidden="true" size={20} />
            {/* 包裹标题和描述。 */}
            <div>
              <h3>提问区</h3>
              <p>Day 8 先搭页面骨架，流式回答会在后续任务接入。</p>
            </div>
          </div>

          {/* 知识库选择：决定本轮问题在哪个企业知识库范围内检索。 */}
          {/* 定义一个表单字段。label 表示这个区域是一个表单项。className="field" 用于控制表单样式。 */}
          <label className="field">
            <span>知识库范围</span>
            {/* value={selectedKnowledgeBaseId} 设置下拉框当前选中的值。这里绑定到状态：selectedKnowledgeBaseId。这叫受控组件，意思为：下拉框显示什么，由 React 状态决定。 */}
            {/* onChange=xxx，当用户切换下拉选项时，执行这个函数。event.target.value 表示用户新选中的值，然后调用：setSelectedKnowledgeBaseId(...) 更新状态。状态更新后，第二个 useEffect 会重新请求 RAG 状态。 */}
            {/* disabled={isLoading} 表示：isLoading 为 true 时，下拉框禁用；isLoading 为 false 时，下拉框可用。*/}
            <select
              value={selectedKnowledgeBaseId}
              onChange={(event) => setSelectedKnowledgeBaseId(event.target.value)}
              disabled={isLoading}
            >
              {/* 遍历知识库列表。 */}
              {knowledgeBases.map((item) => (
                // 定义一个下拉选项。React 列表渲染需要唯一 key。
                // value={item.knowledge_base_id} 这个选项的值是知识库 ID。用户选中这个选项后，event.target.value 就是这个 ID。
                <option key={item.knowledge_base_id} value={item.knowledge_base_id}>
                  {item.name}
                </option>
              ))}
            </select>
          </label>

          {/* 分类选择：all 表示全部分类；具体分类会在后续发送问题时传给后端。 */}
          <label className="field">
            <span>检索分类</span>
            <select value={selectedCategoryId} onChange={(event) => setSelectedCategoryId(event.target.value)}>
              {/* 定义默认选项。value="all"表示不过滤分类。页面显示：全部分类。 */}
              <option value="all">全部分类</option>
              {/* 遍历分类列表。每个分类生成一个下拉选项。 */}
              {categories.map((item) => (
                // 定义分类选项
                <option key={item.category_id} value={item.category_id}>
                  {item.label}
                </option>
              ))}
            </select>
          </label>

          {/* 问题输入区：当前先保留静态 textarea，留给你练习受控组件。 */}
          {/* 定义问题输入字段。 */}
          <label className="field">
            <span>员工问题</span>
            {/* row={6} 表示默认6行高度。 */}
            <textarea placeholder="例如：打车报销需要什么材料？" rows={6} value={question} onChange={(event) => setQuestion(event.target.value)} />
          </label>

          {/* 发送按钮：等你接入 question 状态和 SSE 请求后再取消 disabled。 */}
          <button className="primary-button" type="button" disabled={!question.trim()}>
            <SendHorizontal aria-hidden="true" size={18} />
            发送问题
          </button>
        </section>

        {/* 右侧：展示当前知识库的可检索文档状态。 */}
        {/* aside 表示辅助信息区域。 */}
        <aside className="panel evidence-panel">
          {/* 定义右侧面板标题区域。 */}
          <div className="panel-heading">
            <FileSearch aria-hidden="true" size={20} />
            {/* 包裹右侧面板标题。 */}
            <div>
              <h3>当前检索上下文</h3>
              <p>这里帮助你确认“问答范围”是否已经从 session 独立到企业知识库。</p>
            </div>
          </div>

          {/* 用 dl 展示键值对信息，例如文档数、chunk 数。 */}
          {/* 用 dl 展示元信息。dl 是 HTML 的描述列表。适合展示：字段名：字段值 */}
          <dl className="meta-list">
            {/* 一个键值对容器。 */}
            <div>
              {/* dt 表示描述项名称。 */}
              <dt>知识库</dt>
              {/* dd 表示描述项的值。
              这里显示当前知识库名称。selectedKnowledgeBase?.name 表示：如果 selectedKnowledgeBase 存在，就取 name。如果不存在，就返回 undefined。
              ?? '加载中'：如果左边是 null 或 undefined，就显示”加载中“。*/}
              <dd>{selectedKnowledgeBase?.name ?? '加载中'}</dd>
            </div>
            <div>
              <dt>分类</dt>
              {/* 当 selectedCategoryId = 'all' 时，通常找不到对应分类对象，所以会显示“全部分类”。 */}
              <dd>{selectedCategory?.label ?? '全部分类'}</dd>
            </div>
            <div>
              <dt>文档数</dt>
              <dd>{ragStatus?.document_count ?? 0}</dd>
            </div>
            <div>
              <dt>Chunk 数</dt>
              <dd>{ragStatus?.chunk_count ?? 0}</dd>
            </div>
          </dl>

          {/* 展示当前知识库中的前 5 个文档，避免列表过长影响布局。 */}
          {/* 定义文件列表容器。 */}
          <div className="file-list">
            {/* ragStatus?.documents ?? [] 保证最终一定是数组。然后 slice(0, 5) 表示只取前5个。.map((document) => ( ： 遍历这 5 个文档，每个文档渲染一行。 */}
            {(ragStatus?.documents ?? []).slice(0, 5).map((document) => (
              // 定义一行文件信息。
              <div className="file-row" key={`${document.file_name}-${document.knowledge_base_type}`}>
                <Database aria-hidden="true" size={16} />
                <span>{document.file_name ?? '未命名文档'}</span>
                <small>{document.knowledge_base_type ?? 'general'}</small>
              </div>
            ))}

            {/* 没有文档时展示空状态提示。 */}
            {/* 下面为条件渲染。
            ragStatus?.documents?.length 表示文档数组长度。前面的 ?. 表示安全访问：ragStatus 不存在时，不会报错；documents 不存在时，也不会报错。
            !ragStatus?.documents?.length 表示没有文档。如果没有文档，就显示 p 标签，否则显示 null，也就是什么都不显示。*/}
            {!ragStatus?.documents?.length ? <p className="muted-text">当前知识库还没有可展示的文档状态。</p> : null}
          </div>
        </aside>
      </div>
    </section>
  )
}
