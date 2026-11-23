import type { ChatMessage, AgentStreamEvent } from '../types/chat'
import { answerBody } from './sessionMessages'

// Presentation only: retain the original answer for history, citations and evaluation.
export function visibleAnswer(content: string, streaming = false) {
  let result = content.replace(/(?:\[|【)\s*来源\s*[:：][^\]】\r\n]*[\]】]/g, '')
  if (streaming) result = result.replace(/(?:\[|【)\s*来源\s*[:：][^\]】\r\n]*$/, '')
  return result.replace(/[ \t]+\n/g, '\n').trim()
}

export function shouldSendOnEnter(event: { key: string; shiftKey?: boolean; ctrlKey?: boolean; metaKey?: boolean;
  altKey?: boolean; isComposing?: boolean; keyCode?: number; repeat?: boolean }, question: string, blocked: boolean) {
  return event.key === 'Enter' && !event.shiftKey && !event.ctrlKey && !event.metaKey && !event.altKey
    && !event.isComposing && event.keyCode !== 229 && !event.repeat && Boolean(question.trim()) && !blocked
}

export function interruptAnswer(message: ChatMessage, notice: string): ChatMessage {
  return message.status === 'complete' ? message : { ...message, status: 'error', notice }
}

export function findRecoveredAnswer(messages: ChatMessage[], question: string, afterId: number) {
  const candidates = messages.filter(message => message.role === 'user' && message.content === question
    && (message.messageId ?? 0) > afterId)
  if (candidates.length !== 1) return undefined
  const index = messages.indexOf(candidates[0])
  const answer = messages[index + 1]
  return answer?.role === 'assistant' && answer.status === 'complete' && answer.messageId ? answer : undefined
}

export function applyAnswerEvent(message: ChatMessage, event: AgentStreamEvent): ChatMessage {
  if (event.event_type === 'delta' && event.content) return { ...message, content: message.content + event.content }
  if (event.event_type === 'final') return { ...message, status: 'complete', messageId: event.message_id ?? undefined,
    content: answerBody(event.content || message.content), metadata: event.metadata }
  if (event.event_type === 'error') return { ...interruptAnswer(message,
    event.error_message || event.content || '回答暂未完成，请稍后核对会话。'), metadata: event.metadata ?? message.metadata }
  if (['step_start', 'step_complete', 'workflow_start'].includes(event.event_type)) {
    return { ...message, steps: [...(message.steps ?? []), event.content ?? ''] }
  }
  return message
}
