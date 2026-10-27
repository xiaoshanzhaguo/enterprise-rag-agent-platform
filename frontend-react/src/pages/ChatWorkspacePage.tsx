import { useCallback, useEffect, useRef, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { MessageSquare, Pencil, Plus, RefreshCw, Trash2 } from 'lucide-react'
import { ChatPage } from './ChatPage'
import { createSession, deleteSession, fetchSession, fetchSessions, renameSession } from '../api/sessionApi'
import { restoreMessages } from '../api/sessionMessages'
import { formatApiError } from '../api/httpClient'
import { EmptyState, ErrorNotice } from '../components/WorkspaceUI'
import type { ChatSessionDetail, ChatSessionSummary } from '../types/chat'

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
  const [revision, setRevision] = useState(0)
  const [detailRevision, setDetailRevision] = useState(0)
  const [loaded, setLoaded] = useState<ChatSessionDetail | null>(null)
  const [detailError, setDetailError] = useState('')
  const [detailLoading, setDetailLoading] = useState(false)
  const [busy, setBusy] = useState(false)
  const busyRef = useRef(false)
  const [streaming, setStreaming] = useState<string[]>([])
  const [action, setAction] = useState<'rename' | 'delete' | null>(null)
  const [actionId, setActionId] = useState('')
  const [title, setTitle] = useState('')
  const onSettled = useCallback(() => setRevision(value => value + 1), [])
  const onStreamingChange = useCallback((id: string, isBusy: boolean) => {
    setStreaming(ids => isBusy ? [...new Set([...ids, id])] : ids.filter(value => value !== id))
  }, [])

  useEffect(() => {
    let ignore = false
    const controller = new AbortController()
    const timer = setTimeout(() => controller.abort(), 15000)
    async function load() {
      setListLoading(true); setSessionError('')
      try {
        const result = await fetchSessions(controller.signal)
        if (ignore) return
        setSessions(result.sessions)
        if (!activeId && result.sessions.length) setParams({ session: result.sessions[0].session_id }, { replace: true })
      } catch (error) { if (!ignore) setSessionError(sessionErrorText(error, '会话列表暂不可用，请重试。')) }
      finally { if (!ignore) setListLoading(false); clearTimeout(timer) }
    }
    void load()
    return () => { ignore = true; controller.abort(); clearTimeout(timer) }
  }, [activeId, revision, setParams])

  useEffect(() => {
    if (!activeId) return
    let ignore = false
    const controller = new AbortController()
    const timer = setTimeout(() => controller.abort(), 15000)
    async function load() {
      setDetailLoading(true); setDetailError(''); setLoaded(null)
      try {
        const result = await fetchSession(activeId, controller.signal)
        if (!ignore) setLoaded(result)
      } catch (error) { if (!ignore) setDetailError(sessionErrorText(error, '无法恢复会话，请刷新历史或新建会话。')) }
      finally { if (!ignore) setDetailLoading(false); clearTimeout(timer) }
    }
    void load()
    return () => { ignore = true; controller.abort(); clearTimeout(timer) }
  }, [activeId, detailRevision])

  async function create() {
    if (busyRef.current) return
    busyRef.current = true
    setBusy(true); setSessionError(''); setAction(null)
    const id = `chat-${crypto.randomUUID()}`
    try { await createSession(id); setParams({ session: id }); onSettled() }
    catch (error) { setSessionError(sessionErrorText(error, '创建会话失败，请刷新列表核对后重试。')) }
    finally { busyRef.current = false; setBusy(false) }
  }

  async function confirmAction() {
    if (busyRef.current || !activeId || activeId !== actionId || streaming.includes(activeId)) return
    busyRef.current = true
    const targetId = activeId
    setBusy(true); setSessionError('')
    try {
      if (action === 'rename') {
        await renameSession(targetId, title.trim())
        setLoaded(current => current?.session_id === targetId ? { ...current, title: title.trim() } : current)
      } else if (action === 'delete') {
        await deleteSession(targetId)
        const next = sessions.find(session => session.session_id !== targetId)
        setSessions(current => current.filter(session => session.session_id !== targetId))
        setLoaded(null)
        setParams(next ? { session: next.session_id } : {}, { replace: true })
      }
      setAction(null); onSettled()
    } catch (error) { setSessionError(sessionErrorText(error, '操作未完成，请刷新列表核对后重试。')) }
    finally { busyRef.current = false; setBusy(false) }
  }

  const current = sessions.find(session => session.session_id === activeId)
  const canEdit = !!activeId && !busy && !streaming.includes(activeId) && loaded?.session_id === activeId
  return <section className="page-stack">
    <header className="page-header"><div><p className="eyebrow">KNOWLEDGE / 会话工作区</p><h2>{current?.title || (loaded?.session_id === activeId && loaded.title) || '企业知识问答'}</h2><p>按会话整理问题，恢复历史回答与引用依据。</p></div>
      <div className="session-actions"><button className="ghost-button" disabled={!canEdit} onClick={() => { setActionId(activeId); setTitle(current?.title || loaded?.title || '新会话'); setAction('rename') }}><Pencil size={15} />重命名</button>
        <button className="ghost-button danger-button" disabled={!canEdit} onClick={() => { setActionId(activeId); setAction('delete') }}><Trash2 size={15} />删除会话</button></div></header>
    {action && actionId === activeId && <section className="panel session-confirm" aria-label={action === 'rename' ? '重命名会话' : '删除确认'}>
      {action === 'rename' ? <form onSubmit={event => { event.preventDefault(); void confirmAction() }}><label className="field"><span>会话名称（最多 80 字）</span><input autoFocus value={title} maxLength={80} disabled={busy} onChange={event => setTitle(event.target.value)} /></label>
        <div className="session-actions"><button type="submit" className="primary-button" disabled={busy || !title.trim()}>保存名称</button><button type="button" className="ghost-button" disabled={busy} onClick={() => setAction(null)}>取消</button></div></form>
        : <><h3>确认删除“{current?.title || loaded?.title || '此会话'}”？</h3><p>会话、消息与其临时文档将被删除，无法恢复。公共知识库与流程执行记录不会被删除。</p><div className="session-actions"><button className="ghost-button danger-button" disabled={busy} onClick={() => void confirmAction()}>确认删除</button><button className="ghost-button" disabled={busy} onClick={() => setAction(null)}>取消</button></div></>}
    </section>}
    <div className="session-workspace"><aside className="panel session-sidebar" aria-label="会话列表">
      <div className="section-title"><strong><MessageSquare size={16} />最近会话</strong><button className="text-button" aria-label="刷新会话列表" disabled={listLoading} onClick={onSettled}><RefreshCw size={15} /></button></div>
      <button className="primary-button" disabled={busy} onClick={() => void create()}><Plus size={16} />新建会话</button>
      {sessionError && <ErrorNotice message={sessionError} retry={onSettled} />}
      {listLoading && !sessions.length ? <p role="status">正在读取会话…</p> : <div className="session-list">{sessions.map(session => <button key={session.session_id} className={`session-item ${activeId === session.session_id ? 'active' : ''}`} disabled={busy}
        onClick={() => { setAction(null); setParams({ session: session.session_id }) }} aria-current={activeId === session.session_id ? 'true' : undefined}>
        <strong>{session.title}</strong><small>{streaming.includes(session.session_id) ? '生成中' : `${session.message_count} 条消息`}</small></button>)}</div>}
      <p className="page-footnote">显示最近 50 个问答会话。切换会中断当前前端接收，不会自动重发问题。</p>
    </aside><div className="conversation-surface">
      {!activeId ? <section className="panel"><EmptyState title="开始一段新对话" action={<button className="primary-button" disabled={busy} onClick={() => void create()}>新建会话</button>}>会话会保存到服务器，刷新后可继续查看。</EmptyState></section>
        : detailError ? <ErrorNotice message={detailError} retry={() => setDetailRevision(v => v + 1)} />
          : detailLoading || !loaded || loaded.session_id !== activeId ? <section className="panel" role="status">正在恢复会话…</section>
            : <ChatPage key={`${activeId}-${detailRevision}`} sessionId={activeId} initialMessages={restoreMessages(loaded)} onSettled={onSettled} onStreamingChange={onStreamingChange} onReload={() => setDetailRevision(v => v + 1)} />}
    </div></div>
  </section>
}
