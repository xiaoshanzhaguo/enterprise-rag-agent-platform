import type { AgentStreamEvent, AgentStreamRequest } from '../types/chat'
import { API_BASE_URL, ApiError } from './httpClient'

export const AGENT_STREAM_ENDPOINT = '/agent_stream'
export const buildAgentStreamUrl = () => `${API_BASE_URL}${AGENT_STREAM_ENDPOINT}`
export const buildAgentStreamRequest = (request: AgentStreamRequest) => request

export async function streamAgentAnswer(request: AgentStreamRequest, onEvent: (event: AgentStreamEvent) => void, signal?: AbortSignal) {
  const response = await fetch(buildAgentStreamUrl(), {
    method: 'POST', signal,
    headers: { Accept: 'text/event-stream', 'Content-Type': 'application/json' },
    body: JSON.stringify(buildAgentStreamRequest(request)),
  })
  if (!response.ok) {
    const payload = await response.json().catch(() => null)
    throw new ApiError(`问答请求失败：${response.status}`, response.status, payload)
  }
  if (!response.body || !response.headers.get('content-type')?.includes('text/event-stream')) {
    throw new Error('未收到有效流式响应，请刷新历史核对。')
  }
  const reader = response.body.getReader()
  const decoder = new TextDecoder('utf-8')
  let buffer = ''
  let terminal = false
  function consume(text: string) {
    if (signal?.aborted) throw new DOMException('Aborted', 'AbortError')
    const data = text.split(/\r?\n/).filter(line => line.startsWith('data:')).map(line => line.slice(5).trimStart()).join('\n')
    if (!data) return
    const event = JSON.parse(data) as AgentStreamEvent
    if (event.session_id && event.session_id !== request.session_id) throw new Error('响应会话编号不匹配，已停止显示。')
    if (terminal) return
    terminal = event.event_type === 'final' || event.event_type === 'error'
    onEvent(event)
  }
  try {
    while (true) {
      const { value, done } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      const parts = buffer.split(/\r?\n\r?\n/)
      buffer = parts.pop() || ''
      parts.forEach(consume)
    }
    buffer += decoder.decode()
    if (buffer.trim()) consume(buffer)
    if (!terminal) throw new Error('连接已中断，尚未收到完整回答。请刷新历史核对，不会自动重发。')
  } finally {
    await reader.cancel().catch(() => undefined)
    reader.releaseLock()
  }
}
