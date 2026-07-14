/*
 * 核心员工问答页面
 *
 * 功能描述：
 * 1. 员工在这里选择企业知识库和检索分类。
 * 2. 页面会从后端读取知识库列表、分类列表、当前知识库的 RAG 文档状态。
 * 3. 后续再接入真正的提问和流式回答。
 * 4. 当前已经接入 POST SSE 流式问答，用于展示 Agent 判断、检索过程和 AI 回答。
 */

// Bot             机器人图标，用于提问区
// Database        数据库图标，用于文档或知识库
// FileSearch      文件检索图标，用于检索上下文区域
// SendHorizontal  发送图标，用于发送按钮
import { Bot, Database, FileSearch, SendHorizontal, Delete } from 'lucide-react'
// useState 保存知识库列表、分类列表、当前选中项、RAG 状态、loading、错误信息。
// useEffect 页面加载时请求接口，或知识库切换时重新请求状态。
// useMemo 根据当前 ID 从列表里找出当前选中的完整对象。
import { useEffect, useMemo, useState } from 'react'
// streamAgentAnswer 负责调用后端 /agent_stream，并用 ReadableStream 逐步解析 SSE 事件。
import { streamAgentAnswer } from '../api/chatApi'
import { fetchKnowledgeBaseCategories, fetchKnowledgeBases, fetchRagStatus } from '../api/knowledgeBaseApi'
import type { AgentStreamEvent, AgentTracePanelState, ChatMessage, RagPreviewChunk } from '../types/chat'
import type { KnowledgeBaseCategory, KnowledgeBaseItem, RagStatusResponse } from '../types/knowledgeBase'


// 当前 React 页面只是预览版，还没有真正创建聊天 session。所以先写死一个 session_id，用它去调用 RAG 状态接口。
// 后续真正接入聊天功能后，可能会由后端创建真实会话 ID。
const PREVIEW_SESSION_ID = 'react-preview-session'
// 智能问答页使用的会话 ID。当前先固定，后续接历史会话时可以由后端创建或从 URL 中读取。
const CHAT_SESSION_ID = 'react-chat-session'

// 员工聊天页默认只展示最终答案，不展示 Agent 路由、检索、生成等中间步骤。
// 这些步骤仍然会保存在 message.steps 中，后续可以放到“执行日志”或调试开关里展示。
const SHOULD_SHOW_AGENT_STEPS = false

// 后端判断知识库步骤名称，对应 backend/services/agent_service.py 里的 judge_knowledge。
const STEP_JUDGE_KNOWLEDGE = 'judge_knowledge'

// 后端检索证据步骤名称，对应 backend/services/agent_service.py 里的 retrieve_evidence。
const STEP_RETRIEVE_EVIDENCE = 'retrieve_evidence'

// 后端生成回答步骤名称，对应 backend/services/agent_service.py 里的 generate_answer。
const STEP_GENERATE_ANSWER = 'generate_answer'

// agentTrace 状态标签映射表
const agentTraceStatusLabelMap: Record<AgentTracePanelState['status'], string> = {
  idle: '等待提问',
  running: '执行中',
  complete: '已完成',
  error: '失败'
}

// 生成前端消息 ID。这里用时间戳 + 随机数，足够支撑当前演示版消息列表。
// 它主要用于前端消息列表里的 key，或者用于区分每一条聊天消息。
function buildChatMessageId(prefix: string) {
  // prefix 通常用来表示这条消息属于谁：user 用户消息；assistant AI 回复消息；system 系统消息。
  // toString(16) 表示：把随机小数转换成 16 进制字符串。如：0.6391827364 -> 0.a3f91c8e2b
  // slice(2) 从下标 2 开始截取到末尾，即不要前面的0.
  return `${prefix}-${Date.now()}-${Math.random().toString(16).slice(2)}`
}

// 创建一份新的 Agent 过程面板初始状态。
function buildInitialAgentTrace(): AgentTracePanelState {
  // 返回 running 状态，让右侧面板在用户点击发送后立即显示“正在执行”。
  return {
    status: 'running', // 当前 Agent 正在运行。
    workflowText: '等待 Agent 启动。', // 工作流启动前的占位文案。
    judgeText: '等待 Agent 判断是否需要知识库。', // 判断步骤占位文案。
    retrieveText: '等待检索证据。', // 检索步骤占位文案。
  }
}

// 把后端检索方式转换成更适合页面展示的中文标签。
function formatRetrievalMode(mode?: string) {
  // vector 表示向量语义检索。
  if (mode === 'vector') {
    return '向量检索'
  }

  // keyword 表示关键词兜底检索。
  if (mode === 'keyword') {
    return '关键词检索'
  }

  // no_hit 表示没有可靠命中。
  if (mode === 'no_hit') {
    return '未命中'
  }

  // hybrid 表示混合检索。
  if (mode === 'hybrid') {
    return '混合检索'
  }

  // rerank 表示重排序、重排，完整的说法：检索结果重排。
  // rerank 的意思不是普通地“重新排序一下”，而是对已经召回的候选文档 / chunk 按相关性再次打分排序。
  if (mode === 'rerank') {
    return '重排序'
  }

  return mode || '未知'
}

// 根据检索方式显示分数含义。
function formatChunkScore(chunk: RagPreviewChunk) {
  // 读取后端返回的分数；向量检索里通常是相似度，关键词检索里通常是命中分。
  const score = chunk.score ?? 0

  // 向量检索分数用“相似度”描述，更贴近语义检索语境。
  if (chunk.retrieval_mode === 'vector') {
    return `相似度 ${score}`
  }

  // 关键词检索分数用“命中分”描述，避免误以为它也是向量相似度。
  if (chunk.retrieval_mode === 'keyword') {
    return `命中分 ${score}`
  }

  // 其他检索方式暂时使用通用分数文案。
  return `分数 ${score}`
}

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

  // 保存聊天窗口里的消息列表。
  const [messages, setMessages] = useState<ChatMessage[]>([])

  // 保存右侧 Agent 过程与引用来源面板的数据。
  const [agentTrace, setAgentTrace] = useState<AgentTracePanelState | null>(null)

  // 标记当前是否正在等待后端流式回答。
  const [isStreaming, setIsStreaming] = useState(false)

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

  // 从 Agent 过程状态里取出结构化路由信息。
  // agentTrace 是右侧 Agent 过程面板的状态。metadata 是后端 final 事件返回的结构化数据。
  // agent_route 通常表示 Agent 的路由判断结果。比如：是否需要检索知识库、选择了哪个知识库、选择了哪种检索方式、为什么这么判断。
  const currentAgentRoute = agentTrace?.metadata?.agent_route

  // 从 Agent 过程状态里取出本轮命中的引用 chunk 列表。
  // rag_preview_chunks 通常表示本轮 RAG 检索命中的知识片段列表。
  const currentRagPreviewChunks = agentTrace?.metadata?.rag_preview_chunks ?? []

  // 把后端 SSE 事件同步到右侧 Agent 过程面板。
  // AgentTrace = Agent 过程状态
  // event: AgentStreamEvent 表示传进来的 event 是后端 SSE 解析出来的一条事件。
  function applyStreamEventToAgentTrace(event: AgentStreamEvent) {
    // workflow_start 表示本轮 Agent 流程已经开始。
    if (event.event_type === 'workflow_start') {
      // 用函数式更新，避免流式事件连续到达时拿到旧状态。
      // currentTrace：表示最新的 agentTrace 状态。
      setAgentTrace((currentTrace) => ({
        ...(currentTrace ?? buildInitialAgentTrace()), // 如果当前还没有状态，就先创建一份初始状态。
        status: 'running', // 标记右侧面板处于运行中。
        workflowText: event.content || 'Agent 已开始。', // 保存工作流启动文案。
      }))
      // 当前事件处理完后直接返回，避免继续往下判断。
      return
    }

    // step_start 表示某个步骤开始，右侧面板可以展示更及时的状态。
    // step_start 表示 Agent 的某个步骤开始执行了。比如：开始判断是否需要知识库、开始检索证据、开始生成回答。
    if (event.event_type === 'step_start') {
      // 用函数式更新，确保不会覆盖其他已经到达的步骤信息。
      setAgentTrace((currentTrace) => {
        // 没有旧状态时创建初始状态。
        // 确保后面一定有一个可用的 AgentTrace 状态对象。
        const nextTrace = currentTrace ?? buildInitialAgentTrace()

        // 判断步骤开始时，更新判断区域文案。
        // 判断当前开始的是不是“判断是否需要知识库”
        if (event.step_name === STEP_JUDGE_KNOWLEDGE) {
          // 如果当前是判断步骤，就返回一个新的 AgentTrace 状态对象。
          return {
            ...nextTrace,  // 先复制已有状态。即保留 workflowText、retrieveText、generateText、metadata 等已有字段。
            status: 'running',  // 标记 Agent 仍然处于运行中。
            judgeText: event.content || '正在判断是否需要知识库。',  // 更新判断步骤的展示文案。
          }
        }

        // 检索步骤开始时，更新检索区域文案。
        // 判断当前开始的是不是“检索证据”步骤。STEP_RETRIEVE_EVIDENCE 可能表示 retrieve_evidence，也就是从知识库里检索相关 chunk / 证据片段。
        if (event.step_name === STEP_RETRIEVE_EVIDENCE) {
          return {
            ...nextTrace,
            status: 'running',
            retrieveText: event.content || '正在检索证据。',
          }
        }

        // 生成步骤开始时，只更新生成状态，不把内容放进聊天气泡。（右侧面板只展示“正在生成回答”这类过程信息；真正的聊天气泡内容，通常由 delta 事件单独追加。）
        // 判断当前开始的是不是“生成回答”步骤。STEP_GENERATE_ANSWER 表示：大模型开始生成最终回答。
        if (event.step_name === STEP_GENERATE_ANSWER) {
          return {
            ...nextTrace,
            status: 'running',
            generateText: event.content || '正在生成回答。',
          }
        }

        // 未知步骤暂时保持原状态。
        // 这是一种兜底逻辑：后端传了未知步骤，前端先不报错，也不乱展示。
        return nextTrace
      })
      // 当前事件处理完后直接返回。
      // 表示 step_start 事件处理完毕，直接结束函数。
      return
    }

    // step_complete 表示某个步骤完成，后端 content 里通常有可解释的文本。
    // step_complete 表示某个 Agent 步骤已经执行完成。比如：判断完成、检索完成、回答生成完成。
    // 后端的 content 通常会带一些解释文本，比如：已判断该问题需要检索企业知识库。或者 已召回 3 个相关知识片段。
    if (event.event_type === 'step_complete') {
      // 用函数式更新，把步骤完成内容放到右侧面板对应区域。
      setAgentTrace((currentTrace) => {
        // 没有旧状态时创建初始状态。
        const nextTrace = currentTrace ?? buildInitialAgentTrace()

        // 判断步骤完成时，保存 Agent 路由判断结果。
        // 判断当前完成的是不是“知识库判断步骤”
        if (event.step_name === STEP_JUDGE_KNOWLEDGE) {
          // 如果判断步骤完成，就更新 judgeText
          return {
            ...nextTrace,  // 保留已有状态。
            status: 'running', // 虽然某个步骤完成了，但整个 Agent 流程还没结束，所以仍然是 running。
            judgeText: event.content || nextTrace.judgeText, // 步骤完成时，如果没有新的 content，就不要覆盖原来的文案。
          }
        }

        // 检索步骤完成时，保存命中数量、检索方式、优先证据等文本。
        // 判断当前完成的是不是“检索证据”步骤。检索步骤完成后，后端可能返回：已命中 3 个知识片段，使用混合检索，最高相关度 0.86，所以右侧面板可以展示检索结果摘要。
        if (event.step_name === STEP_RETRIEVE_EVIDENCE) {
          return {
            ...nextTrace,
            status: 'running',
            retrieveText: event.content || nextTrace.retrieveText,
          }
        }

        // 生成步骤完成时，保存完成状态，但默认不在右侧展示完整答案，避免重复阅读负担。（最终答案已经会显示在聊天气泡里；右侧面板没必要再展示一遍完整答案；否则用户要重复阅读。）
        // 判断当前完成的是不是“生成回答”步骤。
        if (event.step_name === STEP_GENERATE_ANSWER) {
          return {
            ...nextTrace,
            status: 'running',  // 虽然生成步骤完成了，但整个 SSE 流可能还会发送 final 事件，所以这里仍然保持 running。
            generateText: event.content ? '回答已生成。' : nextTrace.generateText,
          }
        }

        // 未知步骤暂时保持原状态。
        return nextTrace
      })
      // 当前事件处理完后直接返回。
      return
    }

    // final 表示后端已经返回完整元数据，右侧面板可以展示引用 chunk。
    // final 表示本轮 Agent 流程最终结束。通常会在 final 事件里返回结构化元数据，例如：agent_route、rag_preview_chunks、引用来源、检索方式、命中文档。
    if (event.event_type === 'final') {
      // 写入 final metadata，里面包含 agent_route 和 rag_preview_chunks。
      setAgentTrace((currentTrace) => ({
        ...(currentTrace ?? buildInitialAgentTrace()), // 如果没有旧状态，仍然补一份初始结构。这样可以避免 final 事件异常地先到时，前端状态为空导致报错。
        status: 'complete', // 标记本轮 Agent 执行完成。页面可以根据这个状态显示：已完成、取消 loading、展示最终引用来源、恢复发送按钮。
        metadata: event.metadata, // 保存结构化元数据，供右侧面板渲染引用来源。把后端 final 事件里的结构化元数据保存到 AgentTrace 里。
      }))
      // 当前事件处理完后直接返回。
      return
    }

    // error 表示后端流式过程失败，右侧面板展示错误信息。
    // error 表示：后端流式执行过程中出错。比如：模型调用失败、知识库检索失败、SSE 流中断、后端异常、参数错误。
    if (event.event_type === 'error') {
      // 写入错误状态。
      setAgentTrace((currentTrace) => ({
        ...(currentTrace ?? buildInitialAgentTrace()), // 如果没有旧状态，仍然补一份初始结构。
        status: 'error', // 标记本轮 Agent 执行失败。页面可以根据这个状态显示错误样式。
        errorMessage: event.error_message || event.content || 'Agent 执行失败。', // 保存错误文案。
      }))
    }
  }

  // 把后端 SSE 返回的一条条事件，合并到当前 AI 回复消息里。
  // assistantMessageId 表示当前正在生成的 AI 消息 ID。因为页面里可能有很多条消息，前端需要知道：后端这条流式事件，应该更新哪一条 assistant 消息。
  // event：表示后端 SSE 返回的一条事件。
  function applyStreamEventToAssistantMessage(assistantMessageId: string, event: AgentStreamEvent) {
    // delta 是模型真正生成的增量文本，需要拼接到回答正文里。
    // 如果这是模型生成的增量文本，并且内容不为空，就把它拼接到 AI 回复正文里。
    if (event.event_type === 'delta' && event.content) {
      // 基于最新的 currentMessages 来计算新的 messages。
      // 为什么不用 setMessages([...messages, xxx)? 因为流式输出时，事件会连续快速到达。使用函数式更新更安全，可以避免拿到旧状态。
      setMessages((currentMessages) =>
        // 这一行遍历当前消息数组。currentMessages 是当前所有消息。map会返回一个新的数组。
        // 这里的目标是：找到当前正在生成的 assistant 消息，然后更新它；其他消息保持不变。
        currentMessages.map((message) =>
          // 这一行判断当前遍历到的消息，是否就是要更新的 AI 消息。
          // 如果当前消息的 id 等于传进来的 assistantMessageId，说明找到了目标消息。
          message.id === assistantMessageId
            ? {
                // 对象展开语法。意思是：先复制原来的 message 里所有字段。这样做是为了保留原有字段，不要把其他字段丢掉。
                ...message,
                // 更新 content 字段。意思是：把原来的回答正文 message.content 和新收到的 event.content 拼接起来。
                content: message.content + event.content,
              }
            // 如果当前消息不是目标 AI 消息，就原样返回。也就是说：只更新 assistantMessageId 对应的那一条消息；其他消息不动。
            : message,
        ),
      )
      // 处理完 delta 后直接返回。因为一个事情只需要走一种处理逻辑。
      // 如果已经处理了 delta，就不需要继续往下判断 step_start、final、error。
      return
    }

    // step_start / step_complete / workflow_start 是过程事件，放到 steps 里，便于页面展示 Agent 执行过程。
    // 这一行判断当前事件是不是 Agent 执行过程事件。这三个事件分别表示：workflow_start: 工作流开始；step_start: 某个步骤开始; step_complete: 某个步骤完成。
    // 整体意思：如果事件有内容，并且它是工作流开始、步骤开始、步骤完成这类过程事件，就把它加入 steps。
    if (event.content && ['workflow_start', 'step_start', 'step_complete'].includes(event.event_type)) {
      setMessages((currentMessages) =>
        currentMessages.map((message) =>
          message.id === assistantMessageId
            // 如果是目标 AI 消息，就返回一个新对象。
            ? {
                ...message,
                // 更新 steps 字段。它的作用是：把当前事件的 content 追加到 steps 数组末尾。
                // message.steps ?? [] 含义：如果 message.steps 有值，就用 message.steps；如果 message.steps 是 null 或 undefined，就用空数组 []。
                // [...(message.steps ?? []), event.content ?? '']：先复制原来的 steps，再追加当前事件内容 event.content。
                // 这里为什么不用 message.steps.push(event.content)？因为 React 状态更新要尽量保持不可变更新。也就是不要直接修改原数组，而是创建一个新数组。
                steps: [...(message.steps ?? []), event.content ?? ''],
              }
            // 不是目标消息，就原样返回。
            : message,
        ),
      )
      return
    }

    // final 表示本轮流式输出结束。它不是正文增量，而是一个结束信号。
    if (event.event_type === 'final') {
      setMessages((currentMessages) =>
        currentMessages.map((message) =>
          message.id === assistantMessageId
            ? {
                ...message,
                // 把这条 AI 消息的状态改成：'complete'，表示：这条 AI 回复已经生成完成。
                // 这通常会影响页面展示，比如：隐藏 loading 动画、隐藏“生成中”、显示完整回答状态。
                status: 'complete',
              }
            // 其他消息不变
            : message,
        ),
      )
      return
    }

    // error 表示后端在流式过程中报错。比如：模型调用失败、知识库检索失败、参数错误、服务器异常。
    if (event.event_type === 'error') {
      // 更新消息列表
      setMessages((currentMessages) =>
        // 遍历所有消息
        currentMessages.map((message) =>
          // 找到正在生成的 AI 消息
          message.id === assistantMessageId
            // 如果是目标消息，就返回更新后的新消息对象。
            ? {
                // 复制原来的消息字段
                ...message,
                // 这一行设置错误时显示的内容。它用了逻辑或 || 做兜底。
                content: event.error_message || event.content || '流式问答失败，请稍后重试。',
                // 页面可以根据这个状态显示错误样式。
                status: 'error',
              }
            // 不是目标消息就原样返回。
            : message,
        ),
      )
    }
  }

  // 点击发送按钮后，创建用户消息和 AI 占位消息，然后调用后端流式接口，边接收边更新页面。
  async function handleSendQuestion() {
    // 去掉首尾空格，避免把空问题发给后端。
    const trimmedQuestion = question.trim()

    // 空问题或正在生成时不重复提交。
    if (!trimmedQuestion || isStreaming) {
      return
    }

    // 先生成两条消息：一条用户消息，一条待填充的 AI 消息。
    const userMessage: ChatMessage = {
      id: buildChatMessageId('user'), // 这个 ID 用于区分不同消息、React map 渲染时作为 key、后续更新消息时定位。
      role: 'user', // 页面可以根据 role 决定消息显示在左边还是右边，或者使用不同样式。
      content: trimmedQuestion, // 消息内容就是用户输入的问题。使用 trimmedQuestion 可以避免把首尾空格也显示到页面上。
      status: 'complete',  // 用户消息一创建就是完整的。因为用户输入的内容已经确定，不需要流式生成。
    }
    // 生成 AI 消息的 ID。为什么要单独保存这个 ID? 因为后面后端流式返回事件时，需要不断更新这条 AI 消息。后续 delta、step、final、error 都根据这个 assistantMessageId 找到这条 AI 消息。
    const assistantMessageId = buildChatMessageId('assistant')
    // 创建 AI 占位消息。
    // 这里创建一条 AI 消息列表。这条消息一开始是空的，因为真正内容要等后端流式返回。
    const assistantMessage: ChatMessage = {
      id: assistantMessageId,
      role: 'assistant',
      content: '',  // AI 消息正文一开始是空字符串。后面收到 delta 事件后，会不断执行 message.content + event.content 把内容拼进去。
      steps: [],    // 初始化执行步骤为空数组。后面收到 workflow_start、step_start、step_complete 这些过程事件时，会把内容追加到 steps 里。
      status: 'streaming',  // 设置状态为正在生成。页面可以根据这个状态展示：生成中、loading、光标闪烁、禁用发送按钮。
    }

    // 先把消息显示到页面上，让用户马上看到发送结果。
    // 把用户消息和 AI 占位消息追加到消息列表末尾。
    // (currentMessages) => ... 表示基于当前最新消息列表更新。
    // [...currentMessages, userMessage, assistantMessage]: 表示创建一个数组，先复制原来的所有消息，再追加 userMessage，再追加 assistantMessage。
    // 这样做的好处：用户点击发送后，页面马上显示问题；同时出现一条 AI 正在生成的消息，不用等后端返回。
    setMessages((currentMessages) => [...currentMessages, userMessage, assistantMessage])

    // 重置右侧 Agent 过程面板，准备展示本轮判断、检索和引用来源。
    setAgentTrace(buildInitialAgentTrace())

    // 清空输入框。
    setQuestion('')

    // 进入流式生成状态。
    // 把 isStreaming 设置为 true，表示当前正在等待后端生成回答。它通常用于：禁用发送按钮、显示生成中状态、防止重复提交。
    setIsStreaming(true)

    // 调用后端流式接口
    // 因为调用后端接口可能失败，比如：网络断开、后端报错、HTTP 500、流式读取失败，所以用 try/catch 捕获错误。
    try {
      // 调用前面封装的流式请求函数 streamAgentAnswer, 前面的 await 表示：等待整个流式请求结束。
      // 注意这里不是等一个普通 JSON 返回，而是：等待 SSE 流全部读完。
      await streamAgentAnswer(
        // 开始传入请求体对象，这个对象会发送给后端 /agent_stream。
        {
          session_id: CHAT_SESSION_ID,  // 指定当前聊天会话ID。CHAT_SESSION_ID：后端可以用它保存：聊天记录、检索记录、上下文信息。
          task_type: 'agent',  // 当前任务类型固定为: 'agent'，表示这次调用的是 Agent 智能问答流程。
          input_text: trimmedQuestion,  // 发送给后端的用户原始问题，这里使用清理过空格的 trimmedQuestion。
          mode: '企业知识库问答',  // 当前问答模式。后端可以根据 mode 决定不同处理流程。
          history: [],  // 历史消息列表，当前先传空数组。意思是：第一版暂时不带多轮上下文。后续如果要支持多轮对话，可以把之前的用户消息和 AI 消息整理后放进去。
          use_rag: true,  // 表示启用 RAG。即：回答问题时，需要检索企业知识库。
          rag_top_k: 3,  // 表示最多检索返回 3 个相关片段。top_k 可以理解为：从向量库或知识库中找最相关的前 k 条内容。
          // 用户配置项。这些是前端给后端的补充参数。
          user_options: {
            display_text: trimmedQuestion,  // 前端展示文本。这个字段的作用通常是：让后端知道前端展示用的文本是什么。
            knowledge_base_id: selectedKnowledgeBaseId || undefined,  // 指定当前选择的知识库 ID。为什么不直接传空字符串？为什么不直接传空字符串？因为对后端来说：undefined / 不传 表示没有指定知识库，空字符串：可能被误认为传了一个无效 ID。所以这里用 undefined 更干净。
            knowledge_base_type_filter: selectedCategoryId === 'all' ? undefined : selectedCategoryId,  // 指定知识库分类过滤条件。undefined: 意思是不限制分类，让后端检索全部分类。
            user_role: 'employee', // 告诉后端当前用户角色是普通员工。后端可以根据角色做权限判断。
          },
        },
        // 第二个参数：事件回调函数。
        // 每当后端返回一条 SSE 事件，streamAgentAnswer 就会调用这个函数。参数 event 就是解析后的 AgentStreamEvent。
        (event) => {
          // 把这条事件同步到右侧 Agent 过程面板。
          applyStreamEventToAgentTrace(event)

          // 把这条事件应用到当前 AI 消息上。
          // 这里传了两个参数。assistantMessageId: 表示要更新哪条 AI 消息，event: 表示后端返回的流式事件。
          // 这一行的作用：每收到一条后端事件，就更新 assistantMessageId 对应的那条 AI 消息。
          applyStreamEventToAssistantMessage(assistantMessageId, event)
        },
      )
    } catch (error) {
      // 网络错误或后端非 2xx 错误会进入这里。
      // 可能出错的地方包括：fetch 请求失败、后端返回非 2xx、response.body 不存在、ReadableStream 读取失败。这些错误会被捕获到 error 变量里。
      // 开始更新消息列表。这里要把刚才创建的 AI 占位消息改成错误状态。
      setMessages((currentMessages) =>
        currentMessages.map((message) =>
          message.id === assistantMessageId
            ? {
                ...message,
                // 设置错误消息内容。如果 error 是标准 Error 对象，就显示 error.message；否则显示默认错误文案。
                content: error instanceof Error ? error.message : '流式问答请求失败。',
                // 把 AI 消息状态改成错误。页面可以根据这个状态显示红色错误样式。
                status: 'error',
              }
            // 其他消息保持不变。
            : message,
        ),
      )
    } finally {
      // 不管成功还是失败，都退出生成状态。
      // 把流式生成状态改回 false。这样页面可以：恢复发送按钮、隐藏生成中状态、允许用户继续提问。
      setIsStreaming(false)
    }
  }

  // 清空对话按钮会清空当前前端消息列表
  function handleDeleteChat() {
    // 清空聊天消息列表。
    setMessages([])
    // 同时清空右侧 Agent 过程面板，避免残留上一轮引用证据。
    setAgentTrace(null)
  }

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
              <p>支持通过 fetch + ReadableStream 读取后端 POST SSE 流式回答。</p>
            </div>
          </div>

          {/* 聊天窗口：展示用户问题、AI 回答和 Agent 执行过程。 */}
          {/* 当前员工侧默认隐藏 Agent 执行过程，只把最终答案作为聊天正文展示。 */}
          <div className="chat-window">
            {messages.length === 0 ? (
              <div className="chat-empty">
                <strong>还没有对话</strong>
                <span>选择知识库后输入问题，例如“怎么报销？”或“VPN 连不上怎么办？”。</span>
              </div>
            ) : (
              messages.map((message) => (
                <article className={`chat-message ${message.role}`} key={message.id}>
                  <div className="chat-message-meta">
                    <strong>{message.role === 'user' ? '员工' : 'AI 助手'}</strong>
                    {message.status === 'streaming' ? <span>生成中</span> : null}
                    {message.status === 'error' ? <span>失败</span> : null}
                  </div>

                  {/* 可选链?. 它的意思是：如果 message.steps 存在，就继续访问它的 length；如果 message.steps 是 undefined 或 null，就直接返回 undefined，不报错。 */}
                  {SHOULD_SHOW_AGENT_STEPS && message.steps?.length ? (
                    <ul className="chat-steps">
                      {message.steps.map((step, index) => (
                        <li key={`${message.id}-step-${index}`}>{step}</li>
                      ))}
                    </ul>
                  ) : null}

                  <p>{message.content || (message.status === 'streaming' ? '正在等待后端返回内容...' : '')}</p>
                </article>
              ))
            )}
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
          <button className="primary-button" type="button" disabled={!question.trim() || isStreaming} onClick={handleSendQuestion}>
            <SendHorizontal aria-hidden="true" size={18} />
            {isStreaming ? '生成中...' : '发送问题'}
          </button>

          <button className="primary-button" type="button" disabled={!messages.length || isStreaming} onClick={handleDeleteChat}>
            <Delete aria-hidden="true" size={18} />
            {isStreaming ? '生成中...' : '清空对话'}
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
              <h3>Agent 过程与引用来源</h3>
              <p>这里展示本轮 Agent 判断、检索证据、来源文档和命中分数。</p>
            </div>
          </div>

          {/* Agent 过程面板：展示本轮问答的路由判断、检索结果和生成状态。 */}
          <div className="agent-trace-panel">
            {/* 面板顶部状态条：告诉用户当前 Agent 是等待、运行、完成还是失败。 */}
            <div className="agent-trace-status">
              {/* 左侧状态名称。 */}
              <strong>本轮 Agent 状态</strong>
              {/* 右侧状态标签。 */}
              {/* 如果 agentTrace 没有 status，返回 undefined，最终会显示“等待提问”。 */}
              <span>{agentTraceStatusLabelMap[agentTrace?.status ?? 'idle']}</span>
            </div>

            {/* 没有开始提问时，展示空状态提示。 */}
            {!agentTrace ? (
              <p className="muted-text">发送问题后，这里会展示 Agent 判断结果、检索到的 chunk 和来源文档。</p>
            ) : null}

            {/* 有 Agent 过程数据时，展示判断与检索详情。 */}
            {agentTrace ? (
              <div className="agent-trace-sections">
                {/* Agent 判断结果：展示是否需要知识库、检索 query、判断理由等。 */}
                <section className="trace-section">
                  {/* 小节标题。 */}
                  <h4>Agent 判断结果</h4>
                  {/* 结构化路由字段，比纯文本更适合快速扫描。 */}
                  <dl className="trace-meta-list">
                    {/* 是否需要知识库。 */}
                    <div>
                      <dt>是否检索</dt>
                      <dd>{currentAgentRoute ? (currentAgentRoute.need_knowledge_base ? '需要知识库' : '不需要知识库') : '判断中'}</dd>
                    </div>
                    {/* 实际检索方式。 */}
                    <div>
                      <dt>检索方式</dt>
                      <dd>{formatRetrievalMode(currentAgentRoute?.retrieval_mode)}</dd>
                    </div>
                    {/* 最终用于检索的问题。 */}
                    <div>
                      <dt>检索问题</dt>
                      <dd>{currentAgentRoute?.effective_retrieval_query || currentAgentRoute?.rewritten_query || '暂无'}</dd>
                    </div>
                    {/* 分类过滤条件。 */}
                    <div>
                      <dt>检索分类</dt>
                      <dd>{currentAgentRoute?.knowledge_base_type_filter || '全部'}</dd>
                    </div>
                  </dl>
                  {/* 后端 step_complete 返回的判断说明，保留给需要排查路由时查看。 */}
                  <p className="trace-text">{agentTrace.judgeText || currentAgentRoute?.reason || '等待 Agent 判断结果。'}</p>
                </section>

                {/* 检索证据：展示命中数量、检索方式、优先证据等解释信息。 */}
                <section className="trace-section">
                  {/* 小节标题。 */}
                  <h4>检索命中信息</h4>
                  {/* 检索过程说明文本。 */}
                  <p className="trace-text">{agentTrace.retrieveText || '等待检索结果。'}</p>
                </section>

                {/* 引用来源：展示本轮真正用于回答的 chunk。 */}
                <section className="trace-section">
                  {/* 小节标题。 */}
                  <h4>引用来源 Chunk</h4>
                  {/* 如果有命中 chunk，就逐条展示来源、分数和预览文本。 */}
                  {currentRagPreviewChunks.length ? (
                    <div className="citation-list">
                      {/* 遍历本轮命中的引用 chunk。 */}
                      {currentRagPreviewChunks.map((chunk) => (
                        // 单个引用 chunk 卡片。
                        <article className="citation-card" key={`${chunk.source}-${chunk.rank}`}>
                          {/* chunk 顶部信息：排名和来源。 */}
                          <div className="citation-card-header">
                            {/* 排名。 */}
                            <strong>#{chunk.rank ?? '-'}</strong>
                            {/* 来源文档和 chunk 编号。 */}
                            <span>{chunk.source || `${chunk.file_name ?? '未知文档'}#chunk-${chunk.chunk_id ?? '-'}`}</span>
                          </div>

                          {/* chunk 标签区：检索方式、分数、分类。 */}
                          <div className="citation-tags">
                            {/* 检索方式标签。 */}
                            <small>{formatRetrievalMode(chunk.retrieval_mode)}</small>
                            {/* 相似度或命中分标签。 */}
                            <small>{formatChunkScore(chunk)}</small>
                            {/* 知识库分类标签。 */}
                            <small>{chunk.knowledge_base_type || 'general'}</small>
                          </div>

                          {/* chunk 预览正文。 */}
                          <p>{chunk.text_preview || '暂无片段预览。'}</p>
                        </article>
                      ))}
                    </div>
                  ) : (
                    // 没有命中 chunk 时展示空状态。
                    <p className="muted-text">本轮暂时没有可展示的引用 chunk。</p>
                  )}
                </section>

                {/* 生成状态：说明最终回答是否已经生成，避免和聊天气泡正文重复。 */}
                <section className="trace-section">
                  {/* 小节标题。 */}
                  <h4>生成状态</h4>
                  {/* 生成状态文本。 */}
                  <p className="trace-text">
                    {agentTrace.status === 'error'
                    ? agentTrace.errorMessage || '生成回答失败。'
                    : agentTrace.generateText || '等待生成回答。'}
                  </p>
                </section>
              </div>
            ) : null}
          </div>

          {/* 当前知识库状态：保留原来的文档数、chunk 数和文档列表，帮助确认检索范围。 */}
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
