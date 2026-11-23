import type { AgentStreamEvent, AgentStreamRequest } from '../types/chat'
import { API_BASE_URL, ApiError } from './httpClient'

export const AGENT_STREAM_ENDPOINT = '/agent_stream'
export const buildAgentStreamUrl = () => `${API_BASE_URL}${AGENT_STREAM_ENDPOINT}`
export const buildAgentStreamRequest = (request: AgentStreamRequest) => request

// Bound initial waiting, silent streams and total execution separately.
export const STREAM_LIMITS = { firstResponseMs: 60000, idleMs: 45000, totalMs: 180000 }

export async function streamAgentAnswer(request: AgentStreamRequest, onEvent: (event: AgentStreamEvent) => void,
  signal?: AbortSignal, limits = STREAM_LIMITS) {
  const controller = new AbortController()
  const abort = () => controller.abort(signal?.reason ?? new DOMException('已停止接收', 'AbortError'))
  if (signal?.aborted) abort()
  else signal?.addEventListener('abort', abort, { once: true })
  const totalTimer = setTimeout(() => controller.abort(new DOMException('回答等待时间过长，请核对已保存记录。', 'TimeoutError')), limits.totalMs)
  let reader: ReadableStreamDefaultReader<Uint8Array> | undefined

  function waitFor<T>(operation: Promise<T>, timeoutMs: number): Promise<T> {
    return new Promise((resolve, reject) => {
      const stop = () => { cleanup(); reject(controller.signal.reason) }
      const timer = setTimeout(() => controller.abort(new DOMException('暂未收到新的响应，请核对已保存记录。', 'TimeoutError')), timeoutMs)
      const cleanup = () => { clearTimeout(timer); controller.signal.removeEventListener('abort', stop) }
      operation.then(value => { cleanup(); resolve(value) }, error => { cleanup(); reject(error) })
      if (controller.signal.aborted) { stop(); return }
      controller.signal.addEventListener('abort', stop, { once: true })
    })
  }

  try {
    if (controller.signal.aborted) throw controller.signal.reason
    const response = await waitFor(fetch(buildAgentStreamUrl(), {
      method: 'POST', signal: controller.signal,
      headers: { Accept: 'text/event-stream', 'Content-Type': 'application/json' },
      body: JSON.stringify(buildAgentStreamRequest(request)),
    }), limits.firstResponseMs)
    if (!response.ok) {
      const payload = await waitFor(response.json().catch(() => null), limits.idleMs)
      throw new ApiError(`问答请求失败：${response.status}`, response.status, payload)
    }
    if (!response.body || !response.headers.get('content-type')?.includes('text/event-stream')) {
      throw new Error('未收到有效流式响应，请核对已保存记录。')
    }
    reader = response.body.getReader()
    const decoder = new TextDecoder('utf-8')
    let buffer = ''
    let terminal = false
    function consume(text: string) {
      if (controller.signal.aborted) throw controller.signal.reason
      const data = text.split(/\r?\n/).filter(line => line.startsWith('data:')).map(line => line.slice(5).trimStart()).join('\n')
      if (!data || terminal) return
      const event = JSON.parse(data) as AgentStreamEvent
      if (event.session_id && event.session_id !== request.session_id) throw new Error('响应会话编号不匹配，已停止显示。')
      terminal = event.event_type === 'final' || event.event_type === 'error'
      onEvent(event)
    }
    while (!terminal) {
      const { value, done } = await waitFor(reader.read(), limits.idleMs)
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      const parts = buffer.split(/\r?\n\r?\n/)
      buffer = parts.pop() || ''
      parts.forEach(consume)
    }
    buffer += decoder.decode()
    if (!terminal && buffer.trim()) consume(buffer)
    if (!terminal) throw new Error('连接已中断，尚未收到完整回答。请核对已保存记录，系统不会自动重发。')
  } finally {
    clearTimeout(totalTimer)
    signal?.removeEventListener('abort', abort)
    // Do not wait indefinitely for a broken transport to acknowledge cancellation.
    if (reader) { void reader.cancel().catch(() => undefined); reader.releaseLock() }
    controller.abort()
  }
}
