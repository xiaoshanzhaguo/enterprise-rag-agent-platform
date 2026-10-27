import { apiDelete, apiGet, apiPatch, apiPost } from './httpClient'
import type { ChatSessionDetail, ChatSessionSummary } from '../types/chat'

export const CHAT_MODE = '企业知识库问答'
export const fetchSessions = (signal?: AbortSignal) => apiGet<{ sessions: ChatSessionSummary[] }>(
  `/chat_sessions?limit=50&include_empty=true&mode=${encodeURIComponent(CHAT_MODE)}`, signal)
export const fetchSession = (id: string, signal?: AbortSignal) => apiGet<ChatSessionDetail>(`/chat_session/${encodeURIComponent(id)}`, signal)
export const createSession = (id: string) => apiPost('/chat_session', { session_id: id, mode: CHAT_MODE, title: '新会话' }, AbortSignal.timeout(15000))
export const renameSession = (id: string, title: string) => apiPatch(`/chat_session/${encodeURIComponent(id)}`, { title })
export const deleteSession = (id: string) => apiDelete(`/chat_session/${encodeURIComponent(id)}`)
