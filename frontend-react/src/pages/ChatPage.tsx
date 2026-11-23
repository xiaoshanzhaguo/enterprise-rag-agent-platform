import { Bot, Database, Layers3, RefreshCw, SendHorizontal, SlidersHorizontal, Square } from 'lucide-react'
import { useEffect, useId, useRef, useState } from 'react'
import { AnswerFeedbackForm } from '../components/AnswerFeedbackForm'
import { AnswerSources } from '../components/AnswerSources'
import { ScopeSelect } from '../components/ScopeSelect'
import { streamAgentAnswer } from '../api/chatApi'
import { applyAnswerEvent, findRecoveredAnswer, interruptAnswer, shouldSendOnEnter, visibleAnswer } from '../api/chatPresentation'
import { fetchSession } from '../api/sessionApi'
import { restoreMessages } from '../api/sessionMessages'
import { buildScopedHistory, defaultKnowledgeBaseId, latestMessageId, loadChatDraft, resolveDraftScope, saveChatDraft } from '../api/chatDraft'
import type { SavedChatDraft } from '../api/chatDraft'
import { ApiError, formatApiError } from '../api/httpClient'
import { fetchKnowledgeBaseCategories, fetchKnowledgeBases } from '../api/knowledgeBaseApi'
import type { ChatMessage } from '../types/chat'
import type { KnowledgeBaseCategory, KnowledgeBaseItem } from '../types/knowledgeBase'

export function ChatPage({ sessionId, initialMessages, onSettled, onStreamingChange }: {
  sessionId: string; initialMessages: ChatMessage[]; onSettled: () => void
  onStreamingChange: (id: string, busy: boolean) => void
}) {
  const streamRef = useRef<AbortController | null>(null)
  const recoveryRequest = useRef<AbortController | null>(null)
  const previousUser = initialMessages.at(-1)?.role === 'user' ? initialMessages.at(-1) : undefined
  const recoveryTarget = useRef({ question: previousUser?.content ?? '',
    afterId: Math.max(0, ...initialMessages.filter(message => message !== previousUser).map(message => message.messageId ?? 0)) })
  const mountedRef = useRef(true)
  const composing = useRef(false)
  const questionId = useId()
  const scopeId = useId()
  const messageWindow = useRef<HTMLDivElement>(null)
  const followLatest = useRef(true)
  const historyAtMount = useRef(initialMessages)
  const [knowledgeBases, setKnowledgeBases] = useState<KnowledgeBaseItem[]>([])
  const [categories, setCategories] = useState<KnowledgeBaseCategory[]>([])
  const [draft, setDraft] = useState(() => loadChatDraft(sessionId))
  const draftRef = useRef(draft)
  const { question, selectedKnowledgeBaseId, selectedCategoryId } = draft
  const [draftStorageUnavailable, setDraftStorageUnavailable] = useState(false)
  const [scopeNotice, setScopeNotice] = useState('')
  const [scopeOpen, setScopeOpen] = useState(false)
  const [messages, setMessages] = useState<ChatMessage[]>(initialMessages)
  const [isStreaming, setIsStreaming] = useState(false)
  const [isLoading, setIsLoading] = useState(true)
  const [errorMessage, setErrorMessage] = useState('')
  const [needsReview, setNeedsReview] = useState(Boolean(previousUser
    && (previousUser.messageId === undefined || previousUser.messageId !== draft.skippedQuestionId)))
  const [recovering, setRecovering] = useState(false)
  const [checkedQuestionId, setCheckedQuestionId] = useState<number | null>(null)
  const [recoveryNotice, setRecoveryNotice] = useState(needsReview
    ? '上一条问题尚无完整回答，可能仍在处理。请核对已保存记录，系统不会自动重发。' : '')

  function updateDraft(patch: Partial<SavedChatDraft>) {
    const next = { ...draftRef.current, ...patch }
    draftRef.current = next
    setDraft(next)
    setDraftStorageUnavailable(!saveChatDraft(sessionId, next))
  }

  useEffect(() => {
    mountedRef.current = true
    return () => { mountedRef.current = false; streamRef.current?.abort(); recoveryRequest.current?.abort() }
  }, [])

  useEffect(() => {
    let ignore = false
    async function load() {
      try {
        const [bases, types] = await Promise.all([fetchKnowledgeBases(), fetchKnowledgeBaseCategories()])
        if (ignore) return
        setKnowledgeBases(bases.knowledge_bases)
        setCategories(types.categories)
        const history = historyAtMount.current
        const lastBaseId = [...history].reverse().find(message => message.role === 'assistant')?.metadata?.agent_route?.knowledge_base_id ?? undefined
        const resolved = resolveDraftScope(draftRef.current, bases.knowledge_bases.map(base => base.knowledge_base_id),
          types.categories.map(category => category.category_id), lastBaseId)
        const next = { ...draftRef.current, selectedKnowledgeBaseId: resolved.selectedKnowledgeBaseId,
          selectedCategoryId: resolved.selectedCategoryId,
          historyAfterId: resolved.resetHistory ? Math.max(draftRef.current.historyAfterId, latestMessageId(history)) : draftRef.current.historyAfterId }
        draftRef.current = next; setDraft(next)
        setDraftStorageUnavailable(!saveChatDraft(sessionId, next))
        setScopeNotice(resolved.notice)
      } catch (error) {
        if (!ignore) setErrorMessage(formatApiError(error, '知识库配置加载失败，请重新进入此会话。'))
      } finally { if (!ignore) setIsLoading(false) }
    }
    void load()
    return () => { ignore = true }
  }, [sessionId])

  useEffect(() => {
    const node = messageWindow.current
    if (node && followLatest.current) node.scrollTop = node.scrollHeight
  }, [messages])

  const blocked = isStreaming || isLoading || needsReview || recovering
    || !knowledgeBases.some(base => base.knowledge_base_id === selectedKnowledgeBaseId)
  const customScope = (selectedKnowledgeBaseId && selectedKnowledgeBaseId !== defaultKnowledgeBaseId(knowledgeBases.map(base => base.knowledge_base_id)))
    || selectedCategoryId !== 'all'
  function changeScope(patch: Pick<Partial<SavedChatDraft>, 'selectedKnowledgeBaseId' | 'selectedCategoryId'>) {
    if (streamRef.current || recoveryRequest.current || isLoading || needsReview) return
    if (Object.entries(patch).every(([key, value]) => draftRef.current[key as keyof SavedChatDraft] === value)) return
    updateDraft({ ...patch, historyAfterId: Math.max(draftRef.current.historyAfterId, latestMessageId(messages)) })
    setScopeNotice('已切换范围，请在新范围内重新描述问题；历史消息仍可查看。')
  }
  async function reloadSavedAnswer() {
    if (streamRef.current || recoveryRequest.current) return
    const controller = new AbortController()
    recoveryRequest.current = controller; setRecovering(true); setCheckedQuestionId(null)
    try {
      const session = await fetchSession(sessionId, AbortSignal.any([controller.signal, AbortSignal.timeout(15000)]))
      if (!mountedRef.current || controller.signal.aborted) return
      const restored = restoreMessages(session)
      if (findRecoveredAnswer(restored, recoveryTarget.current.question, recoveryTarget.current.afterId)) {
        setMessages(restored); setNeedsReview(false); setRecoveryNotice('已恢复完整回答，可以继续提问。'); onSettled()
      } else {
        const last = restored.at(-1)
        const { question, afterId } = recoveryTarget.current
        const matchingId = last?.role === 'user' && last.content === question && (last.messageId ?? 0) > afterId
          ? last.messageId ?? 0 : 0
        if (needsReview) setCheckedQuestionId(matchingId)
        setRecoveryNotice('尚未找到这次问题的完整回答。后台可能仍在处理，你可以稍后再查，也可以继续提问。')
      }
    } catch (error) {
      if (mountedRef.current && !controller.signal.aborted) setRecoveryNotice(error instanceof DOMException && error.name === 'TimeoutError'
        ? '查询超时，已保留当前内容，请稍后再次查询。' : formatApiError(error, '查询记录失败，已保留当前内容，请稍后重试。'))
    } finally {
      if (recoveryRequest.current === controller) recoveryRequest.current = null
      if (mountedRef.current) setRecovering(false)
    }
  }

  function continueAfterInterruption() {
    if (!needsReview || checkedQuestionId === null || recoveryRequest.current || streamRef.current) return
    updateDraft({
      ...(checkedQuestionId ? { skippedQuestionId: checkedQuestionId } : {}),
      historyAfterId: Math.max(draftRef.current.historyAfterId, latestMessageId(messages), checkedQuestionId),
    })
    setCheckedQuestionId(null)
    setNeedsReview(false)
    setRecoveryNotice('已保留会话记录，可以继续提问。')
  }

  async function handleSendQuestion() {
    const trimmed = question.trim()
    if (!trimmed || blocked || streamRef.current || recoveryRequest.current) return
    const id = `assistant-${crypto.randomUUID()}`
    const controller = new AbortController()
    // Set the lock synchronously so consecutive key/click events cannot send twice.
    streamRef.current = controller
    setIsStreaming(true)
    onStreamingChange(sessionId, true)
    setErrorMessage('')
    setRecoveryNotice(''); setNeedsReview(false); setCheckedQuestionId(null)
    recoveryTarget.current = { question: trimmed, afterId: Math.max(0, ...messages.map(message => message.messageId ?? 0)) }
    followLatest.current = true
    setMessages(current => [...current,
      { id: `user-${crypto.randomUUID()}`, role: 'user', content: trimmed, status: 'complete' },
      { id, role: 'assistant', content: '', status: 'streaming', steps: [], knowledgeBaseId: selectedKnowledgeBaseId }])
    updateDraft({ question: '' })
    let terminalReceived = false
    try {
      await streamAgentAnswer({
        session_id: sessionId, task_type: 'agent', input_text: trimmed, mode: '企业知识库问答',
        history: buildScopedHistory(messages, selectedKnowledgeBaseId, draftRef.current.historyAfterId),
        use_rag: true, rag_top_k: 3,
        user_options: { display_text: trimmed, knowledge_base_id: selectedKnowledgeBaseId,
          knowledge_base_type_filter: selectedCategoryId === 'all' ? undefined : selectedCategoryId, user_role: 'employee' },
      }, event => {
        if (!mountedRef.current || controller.signal.aborted) return
        terminalReceived = event.event_type === 'final' || event.event_type === 'error'
        setMessages(current => current.map(message => message.id === id ? applyAnswerEvent(message, event) : message))
        if (event.event_type === 'error') {
          setNeedsReview(true)
          setRecoveryNotice('本次回答未完成，可查询已保存记录。系统不会自动重发问题。')
        }
      }, controller.signal)
    } catch (error) {
      if (!mountedRef.current || terminalReceived) return
      const notice = controller.signal.aborted ? '已停止接收。后台可能仍在处理，这不代表任务已取消。'
        : error instanceof DOMException && error.name === 'TimeoutError' ? '等待响应超时，已保留收到的内容。后台可能仍在处理。'
          : formatApiError(error, '连接中断，已保留收到的内容。')
      setMessages(current => current.map(message => message.id === id
        ? interruptAnswer(message, notice) : message))
      const rejected = error instanceof ApiError && [400, 403, 404, 422].includes(error.status)
      setNeedsReview(!rejected); setRecoveryNotice(rejected ? `${notice} 本次请求未被接受，可修改后重新提问。`
        : `${notice} 请核对已保存记录后继续，系统不会自动重发。`)
    } finally {
      streamRef.current = null
      if (mountedRef.current) setIsStreaming(false)
      onStreamingChange(sessionId, false)
      onSettled()
    }
  }

  return <section className="chat-page" aria-label="问答工作区">
    {errorMessage ? <div className="alert error chat-config-error" role="alert">{errorMessage}</div> : null}
    <div className="chat-window" ref={messageWindow} tabIndex={0} aria-label="对话消息"
      onScroll={event => { const node = event.currentTarget; followLatest.current = node.scrollHeight - node.scrollTop - node.clientHeight < 80 }}>
      {!messages.length ? <div className="chat-empty"><Bot size={32} aria-hidden="true" /><strong>有什么可以帮你？</strong>
        <span>可以问我企业制度、报销流程，或 IT 使用问题。</span></div>
        : messages.map(message => <article className={`chat-message ${message.role}`} key={message.id}>
          <div className="chat-message-meta"><strong>{message.role === 'user' ? '我' : '知行助手'}</strong>
            {message.status === 'streaming' ? <span role="status">{message.content ? '正在回答…' : '正在准备回答…'}</span> : message.status === 'error' ? <span>未完成</span> : null}</div>
          <p>{(message.role === 'assistant' ? visibleAnswer(message.content, message.status === 'streaming') : message.content)
            || (message.status === 'streaming' ? '正在查找资料，请稍候…' : '')}</p>
          {message.notice ? <div className="chat-answer-notice" role="status">{message.notice}</div> : null}
          {message.role === 'assistant' && message.status !== 'streaming' ? <AnswerSources sources={message.metadata?.rag_preview_chunks ?? []} /> : null}
          {message.role === 'assistant' && message.status === 'complete' && message.messageId
            ? <AnswerFeedbackForm sessionId={sessionId} messageId={message.messageId} initialFeedback={message.feedback} /> : null}
        </article>)}
    </div>
    {recoveryNotice ? <div className="chat-recovery"><span role="status">{recoveryNotice}</span>
      {needsReview ? <div className="chat-recovery-actions">
        <button type="button" className="text-button" disabled={isStreaming || recovering}
          onClick={() => void reloadSavedAnswer()}><RefreshCw size={14} />{recovering ? '查询中…' : '查询已保存回答'}</button>
        {checkedQuestionId !== null ? <button type="button" className="text-button"
          onClick={continueAfterInterruption}>继续提问</button> : null}
      </div> : null}
    </div> : null}
    <form className="chat-composer" onSubmit={event => { event.preventDefault(); void handleSendQuestion() }}>
      <div className="chat-scope-row" id={scopeId} hidden={!scopeOpen}>
        <ScopeSelect label="知识库范围" icon={<Database size={17} aria-hidden="true" />} value={selectedKnowledgeBaseId} placeholder={isLoading ? '正在加载…' : '请选择'}
          disabled={isLoading || isStreaming || needsReview || recovering} onChange={value => changeScope({ selectedKnowledgeBaseId: value })}
          options={knowledgeBases.map(base => ({ value: base.knowledge_base_id, label: base.name, description: base.description }))} />
        <ScopeSelect label="检索分类" icon={<Layers3 size={17} aria-hidden="true" />} value={selectedCategoryId}
          disabled={isLoading || isStreaming || needsReview || recovering} onChange={value => changeScope({ selectedCategoryId: value })}
          options={[{ value: 'all', label: '自动判断', description: '根据问题判断是否限定分类' },
            ...categories.map(category => ({ value: category.category_id, label: category.label, description: category.description }))]} />
      </div>
      {scopeNotice ? <p className="chat-scope-notice" role="status">{scopeNotice}</p> : null}
      <div className="chat-input-box">
        <button type="button" className="chat-composer-icon chat-scope-toggle" aria-label="检索设置" title="检索设置"
          aria-expanded={scopeOpen} aria-controls={scopeId} data-custom={Boolean(customScope)}
          onClick={() => setScopeOpen(open => !open)}><SlidersHorizontal size={18} aria-hidden="true" /></button>
        <label htmlFor={questionId} className="sr-only">你的问题</label>
        <textarea id={questionId} placeholder="输入你想问的问题…" rows={1} value={question}
          onCompositionStart={() => { composing.current = true }} onCompositionEnd={() => { composing.current = false }}
          onChange={event => updateDraft({ question: event.target.value })} onKeyDown={event => {
            if (shouldSendOnEnter({ key: event.key, shiftKey: event.shiftKey, ctrlKey: event.ctrlKey, metaKey: event.metaKey,
              altKey: event.altKey, repeat: event.repeat, keyCode: event.nativeEvent.keyCode,
              isComposing: event.nativeEvent.isComposing || composing.current }, question, blocked || Boolean(streamRef.current))) {
              event.preventDefault(); void handleSendQuestion()
            }
          }} />
        {isStreaming
          ? <button className="chat-composer-icon chat-stop" type="button" title="停止接收" aria-label="停止接收"
            onClick={() => streamRef.current?.abort()}><Square size={16} aria-hidden="true" /></button>
          : <button className="primary-button chat-send-action" type="submit" aria-label="发送问题" title="发送问题"
            disabled={!question.trim() || blocked}><SendHorizontal size={18} aria-hidden="true" /></button>}
      </div>
      {draftStorageUnavailable ? <p className="chat-draft-warning" role="status">草稿暂存不可用，刷新可能丢失输入。</p> : null}
    </form>
  </section>
}
