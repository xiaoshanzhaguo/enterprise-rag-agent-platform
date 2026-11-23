import { apiDelete, apiGet, apiPatch, apiPost } from './httpClient'
import type { ChatSessionDetail, ChatSessionSummary } from '../types/chat'

export const CHAT_MODE = '企业知识库问答'
export const fetchSessions = (signal?: AbortSignal) => apiGet<{ sessions: ChatSessionSummary[] }>(
  `/chat_sessions?limit=50&include_empty=true&mode=${encodeURIComponent(CHAT_MODE)}`, signal)
export const fetchSession = (id: string, signal?: AbortSignal) => apiGet<ChatSessionDetail>(`/chat_session/${encodeURIComponent(id)}`, signal)
export const fetchSessionPage = (signal?: AbortSignal, cursor?: string) => {
  const params = new URLSearchParams({ limit: '50', include_empty: 'true', mode: CHAT_MODE })
  if (cursor) params.set('cursor', cursor)
  return apiGet<{ sessions: ChatSessionSummary[]; next_cursor: string | null }>(`/chat_session_pages?${params}`, signal)
}

export function mergeSessionSummaries(current: ChatSessionSummary[], incoming: ChatSessionSummary[]) {
  const known = new Set(current.map(session => session.session_id))
  return [...current, ...incoming.filter(session => {
    if (known.has(session.session_id)) return false
    known.add(session.session_id)
    return true
  })]
}
export const createSession = (id: string) => apiPost<{ session_id: string; mode: string; title: string }, { session_id: string }>(
  '/chat_session_draft', { session_id: id, mode: CHAT_MODE, title: '新会话' }, AbortSignal.timeout(15000))
export const renameSession = (id: string, title: string) => apiPatch(`/chat_session/${encodeURIComponent(id)}`, { title })
export const generateSessionTitle = (id: string, signal: AbortSignal) => apiPost<{ session_id: string }, { title: string }>(
  `/chat_session/${encodeURIComponent(id)}/auto_title`, { session_id: id }, signal)
export const deleteSession = (id: string) => apiDelete(`/chat_session/${encodeURIComponent(id)}`)
