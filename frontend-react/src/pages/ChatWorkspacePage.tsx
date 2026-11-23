import { useCallback, useEffect, useRef, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { MessageSquare, Plus, RefreshCw } from 'lucide-react'
import { ChatPage } from './ChatPage'
import { createSession, deleteSession, fetchSession, fetchSessionPage, generateSessionTitle, mergeSessionSummaries, renameSession } from '../api/sessionApi'
import { restoreMessages } from '../api/sessionMessages'
import { clearChatDraft } from '../api/chatDraft'
import { ApiError, formatApiError } from '../api/httpClient'
import { ErrorNotice } from '../components/WorkspaceUI'
import { SessionActionsMenu } from '../components/SessionActionsMenu'
import { SessionActionDialog } from '../components/SessionActionDialog'
import type { ChatSessionDetail, ChatSessionSummary } from '../types/chat'
import '../styles/chat-workspace.css'

function sessionErrorText(error: unknown, fallback: string) {
  if (error instanceof DOMException && ['TimeoutError', 'AbortError'].includes(error.name)) {
    return '会话请求超时，结果可能已保存。请刷新列表或历史核对，系统不会自动重试。'
  }
  return formatApiError(error, fallback)
}

export function ChatWorkspacePage() {
  const [params, setParams] = useSearchParams()
  const activeId = params.get('session') || ''
  const [sessions, setSessions] = useState<ChatSessionSummary[]>([])
  const [sessionError, setSessionError] = useState('')
  const [listLoading, setListLoading] = useState(true)
  const [nextCursor, setNextCursor] = useState<string | null>(null)
  const [moreLoading, setMoreLoading] = useState(false)
  const [moreError, setMoreError] = useState('')
  const listGeneration = useRef(0)
  const moreRequest = useRef<AbortController | null>(null)
  const [revision, setRevision] = useState(0)
  const [detailRevision, setDetailRevision] = useState(0)
  const [loaded, setLoaded] = useState<ChatSessionDetail | null>(null)
  const [detailError, setDetailError] = useState('')
  const [detailLoading, setDetailLoading] = useState(false)
  const [busy, setBusy] = useState(false)
  const busyRef = useRef(false)
  const actionTrigger = useRef<HTMLButtonElement | null>(null)
  const [streaming, setStreaming] = useState<string[]>([])
  const [action, setAction] = useState<'rename' | 'delete' | null>(null)
  const [actionId, setActionId] = useState('')
  const [actionError, setActionError] = useState('')
  const [title, setTitle] = useState('')
  const onSettled = useCallback(() => setRevision(value => value + 1), [])
  const onStreamingChange = useCallback((id: string, isBusy: boolean) => {
    setStreaming(ids => isBusy ? [...new Set([...ids, id])] : ids.filter(value => value !== id))
  }, [])

  useEffect(() => {
    let ignore = false
    listGeneration.current++
    moreRequest.current?.abort()
    moreRequest.current = null
    const controller = new AbortController()
    const timer = setTimeout(() => controller.abort(), 15000)
    async function load() {
      setListLoading(true); setSessionError(''); setMoreError(''); setMoreLoading(false); setNextCursor(null)
      try {
        const result = await fetchSessionPage(controller.signal)
        if (ignore) return
        setSessions(result.sessions)
        setNextCursor(result.next_cursor)
        if (!activeId && result.sessions.length) setParams({ session: result.sessions[0].session_id }, { replace: true })
      } catch (error) { if (!ignore) setSessionError(sessionErrorText(error, '会话列表暂不可用，请重试。')) }
      finally { if (!ignore) setListLoading(false); clearTimeout(timer) }
    }
    void load()
    return () => { ignore = true; controller.abort(); moreRequest.current?.abort(); clearTimeout(timer) }
  }, [activeId, revision, setParams])

  async function loadMore() {
    if (!nextCursor || moreRequest.current || listLoading) return
    const generation = listGeneration.current
    const controller = new AbortController()
    moreRequest.current = controller
    setMoreLoading(true); setMoreError('')
    try {
      const result = await fetchSessionPage(AbortSignal.any([controller.signal, AbortSignal.timeout(15000)]), nextCursor)
      if (controller.signal.aborted || generation !== listGeneration.current) return
      if (result.next_cursor === nextCursor) throw new Error('分页状态未前进，请刷新会话列表。')
      setSessions(current => mergeSessionSummaries(current, result.sessions))
      setNextCursor(result.next_cursor)
    } catch (error) {
      if (!controller.signal.aborted && generation === listGeneration.current) setMoreError(sessionErrorText(error, '读取更早会话失败，请重试。'))
    } finally {
      if (moreRequest.current === controller) { moreRequest.current = null; setMoreLoading(false) }
    }
  }

  useEffect(() => {
    if (!activeId) return
    let ignore = false
    const controller = new AbortController()
    const timer = setTimeout(() => controller.abort(), 15000)
    async function load() {
      setDetailLoading(true); setDetailError(''); setLoaded(null)
      try {
        const result = await fetchSession(activeId, controller.signal)
        if (!ignore) {
          setLoaded(result)
          if ((!result.title?.trim() || ['新会话', '未命名会话'].includes(result.title.trim()))
            && result.messages.some(message => message.role === 'user')) {
            // Repair old placeholder titles without delaying history or retrying model failures.
            void generateSessionTitle(activeId, AbortSignal.any([controller.signal, AbortSignal.timeout(15000)]))
              .then(() => { if (!ignore) onSettled() }).catch(() => {})
          }
        }
      } catch (error) {
        if (ignore) return
        if (error instanceof ApiError && error.status === 404) {
          setParams({}, { replace: true })
          return
        }
        setDetailError(sessionErrorText(error, '无法恢复会话，请重试或新建会话。'))
      }
      finally { if (!ignore) setDetailLoading(false); clearTimeout(timer) }
    }
    void load()
    return () => { ignore = true; controller.abort(); clearTimeout(timer) }
  }, [activeId, detailRevision, onSettled, setParams])

  async function create() {
    if (busyRef.current || streaming.length) return
    busyRef.current = true
    setBusy(true); setSessionError(''); setAction(null)
    const id = `chat-${crypto.randomUUID()}`
    try { const result = await createSession(id); setParams({ session: result.session_id }); onSettled() }
    catch (error) { setSessionError(sessionErrorText(error, '创建会话失败，请刷新列表核对后重试。')) }
    finally { busyRef.current = false; setBusy(false) }
  }

  async function confirmAction() {
    if (busyRef.current || !action || !actionId || streaming.includes(actionId)) return
    busyRef.current = true
    const targetId = actionId
    setBusy(true); setActionError('')
    try {
      if (action === 'rename') {
        await renameSession(targetId, title.trim())
        setSessions(current => current.map(session => session.session_id === targetId ? { ...session, title: title.trim() } : session))
        setLoaded(current => current?.session_id === targetId ? { ...current, title: title.trim() } : current)
      } else if (action === 'delete') {
        await deleteSession(targetId)
        clearChatDraft(targetId)
        const next = sessions.find(session => session.session_id !== targetId)
        setSessions(current => current.filter(session => session.session_id !== targetId))
        if (targetId === activeId) {
          setLoaded(null)
          setParams(next ? { session: next.session_id } : {}, { replace: true })
        }
      }
      setAction(null); onSettled()
    } catch (error) { setActionError(sessionErrorText(error, '操作未完成，请核对后重试。')) }
    finally { busyRef.current = false; setBusy(false) }
  }

  const actionSession = sessions.find(session => session.session_id === actionId)
  return <section className="page-stack chat-workspace-page">
    <header className="page-header"><h2>智能问答</h2></header>
    {action && <SessionActionDialog title={action === 'rename' ? '重命名会话' : '删除确认'} busy={busy} returnFocusTo={actionTrigger} onClose={() => setAction(null)}>
      {actionError && <ErrorNotice message={actionError} />}
      {action === 'rename' ? <form onSubmit={event => { event.preventDefault(); void confirmAction() }}><label className="field"><span>会话名称（最多 80 字）</span><input autoFocus value={title} maxLength={80} disabled={busy} onChange={event => setTitle(event.target.value)} /></label>
        <div className="session-dialog-actions"><button type="button" className="ghost-button" disabled={busy} onClick={() => setAction(null)}>取消</button><button type="submit" className="primary-button" disabled={busy || !title.trim() || streaming.includes(actionId)}>保存名称</button></div></form>
        : <><p>确定删除会话“<strong>{actionSession?.title || title || '此会话'}</strong>”吗？</p><p className="session-delete-notice">会话、消息与其临时文档将被删除，无法恢复。公共知识库与流程执行记录不会被删除。</p><div className="session-dialog-actions"><button type="button" className="ghost-button" disabled={busy} onClick={() => setAction(null)}>取消</button><button type="button" className="primary-button danger-solid-button" disabled={busy || streaming.includes(actionId)} onClick={() => void confirmAction()}>确认删除</button></div></>}
    </SessionActionDialog>}
    <div className="session-workspace"><aside className="session-sidebar" aria-label="会话列表">
      <div className="session-sidebar-actions">
        <button type="button" className="primary-button session-new" disabled={busy || Boolean(streaming.length)} onClick={() => void create()}>
          <Plus size={16} aria-hidden="true" />新建会话
        </button>
        <button type="button" className="session-refresh" aria-label="刷新会话列表" title="刷新会话列表"
          disabled={listLoading} onClick={onSettled}><RefreshCw size={15} aria-hidden="true" /></button>
      </div>
      {sessionError && <ErrorNotice message={sessionError} retry={onSettled} />}
      <h3 className="session-list-label" id="recent-chat-sessions">最近会话</h3>
      {listLoading && !sessions.length ? <p className="session-list-end" role="status">正在读取会话…</p> : <div className="session-list" role="list" aria-labelledby="recent-chat-sessions" tabIndex={0}
        onScroll={event => { const node = event.currentTarget; if (!moreError && node.scrollHeight - node.scrollTop - node.clientHeight < 80) void loadMore() }}>
        {sessions.map(session => <div role="listitem" key={session.session_id} className={`session-row ${activeId === session.session_id ? 'active' : ''}`}>
        <button type="button" className="session-item" disabled={busy}
        onClick={() => { setAction(null); setParams({ session: session.session_id }) }} aria-current={activeId === session.session_id ? 'true' : undefined}>
        <strong title={session.title}>{session.title}</strong>{streaming.includes(session.session_id) && <small>生成中</small>}</button>
        <SessionActionsMenu title={session.title} disabled={busy || streaming.includes(session.session_id)} onAction={(next, trigger) => {
          if (busyRef.current || streaming.includes(session.session_id)) return
          actionTrigger.current = trigger
          setActionId(session.session_id); setTitle(session.title); setActionError(''); setAction(next)
        }} />
      </div>)}
      {moreError ? <p role="alert" className="session-page-error">{moreError}</p> : null}
      {nextCursor ? <button type="button" className="session-load-more" disabled={moreLoading || listLoading} onClick={() => void loadMore()}>
        {moreLoading ? '正在加载…' : moreError ? '重试加载' : '加载更早会话'}
      </button> : !sessions.length && !listLoading && !sessionError ? <p className="session-list-end">还没有会话</p> : null}
      </div>}
    </aside><div className="conversation-surface">
      {!activeId ? <section className="chat-start" aria-label="开始对话">
        <MessageSquare size={30} aria-hidden="true" /><h3>开始一段新对话</h3>
        <button className="primary-button" disabled={busy || Boolean(streaming.length)} onClick={() => void create()}><Plus size={16} />新建会话</button>
      </section>
        : detailError ? <ErrorNotice message={detailError} retry={() => setDetailRevision(v => v + 1)} />
          : detailLoading || !loaded || loaded.session_id !== activeId ? <section className="chat-start" role="status">正在恢复会话…</section>
            : <ChatPage key={`${activeId}-${detailRevision}`} sessionId={activeId} initialMessages={restoreMessages(loaded)} onSettled={onSettled} onStreamingChange={onStreamingChange} />}
    </div></div>
  </section>
}
