import type { ChatMessage, ChatSessionDetail } from '../types/chat'

export function answerBody(content: string) {
  try {
    const value = JSON.parse(content)
    if (value && typeof value.generate_answer === 'string') return value.generate_answer
  } catch { /* 普通正文无需解析。 */ }
  return content
}

export function restoreMessages(session: ChatSessionDetail): ChatMessage[] {
  return session.messages.filter(message => message.role === 'user' || message.role === 'assistant').map((message, index) => ({
    id: `${session.session_id}-history-${index}`, role: message.role as 'user' | 'assistant',
    content: message.role === 'assistant' ? answerBody(message.raw_content || message.content) : message.content,
    status: 'complete',
    metadata: message.role === 'assistant' ? { agent_route: message.agent_route, rag_preview_chunks: message.rag_preview_chunks } : undefined,
  }))
}
