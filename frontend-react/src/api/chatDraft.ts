import type { ChatMessage } from '../types/chat'

export interface SavedChatDraft {
  question: string
  selectedKnowledgeBaseId: string
  selectedCategoryId: string
  historyAfterId: number
  skippedQuestionId?: number
}

type DraftStorage = Pick<Storage, 'getItem' | 'setItem' | 'removeItem'>
const DEFAULT_KNOWLEDGE_BASE_ID = 'enterprise_default'
const fallback = new Map<string, SavedChatDraft>()
const key = (sessionId: string) => `zhixing.chat.draft.v1:${encodeURIComponent(sessionId)}`
const emptyDraft = (): SavedChatDraft => ({ question: '', selectedKnowledgeBaseId: '', selectedCategoryId: 'all', historyAfterId: 0 })

function browserStorage(): DraftStorage | undefined {
  try { return window.sessionStorage } catch { return undefined }
}

export function loadChatDraft(sessionId: string, storage = browserStorage()): SavedChatDraft {
  const memory = fallback.get(sessionId)
  if (memory) return { ...memory }
  try {
    const value = JSON.parse(storage?.getItem(key(sessionId)) || 'null')
    if (value?.version === 1 && typeof value.question === 'string' && typeof value.selectedKnowledgeBaseId === 'string'
      && typeof value.selectedCategoryId === 'string' && Number.isSafeInteger(value.historyAfterId) && value.historyAfterId >= 0) {
      return { question: value.question, selectedKnowledgeBaseId: value.selectedKnowledgeBaseId,
        selectedCategoryId: value.selectedCategoryId, historyAfterId: value.historyAfterId,
        ...(Number.isSafeInteger(value.skippedQuestionId) && value.skippedQuestionId > 0
          ? { skippedQuestionId: value.skippedQuestionId } : {}) }
    }
  } catch { /* A blocked store or malformed record must not prevent asking questions. */ }
  return emptyDraft()
}

export function saveChatDraft(sessionId: string, draft: SavedChatDraft, storage = browserStorage()): boolean {
  try {
    if (!storage) throw new Error('Session storage unavailable')
    storage.setItem(key(sessionId), JSON.stringify({ ...draft, version: 1 }))
    fallback.delete(sessionId)
    return true
  } catch {
    fallback.set(sessionId, { ...draft })
    return false
  }
}

export function clearChatDraft(sessionId: string, storage = browserStorage()) {
  fallback.delete(sessionId)
  try { storage?.removeItem(key(sessionId)) } catch { /* Deleting a conversation still succeeds when storage is blocked. */ }
}

export function defaultKnowledgeBaseId(baseIds: string[]) {
  return baseIds.includes(DEFAULT_KNOWLEDGE_BASE_ID) ? DEFAULT_KNOWLEDGE_BASE_ID : baseIds[0] ?? ''
}

export function resolveDraftScope(draft: SavedChatDraft, baseIds: string[], categoryIds: string[], lastBaseId?: string) {
  let selectedKnowledgeBaseId = draft.selectedKnowledgeBaseId || lastBaseId || ''
  let selectedCategoryId = draft.selectedCategoryId
  const notices: string[] = []
  let resetHistory = false
  if (selectedKnowledgeBaseId && !baseIds.includes(selectedKnowledgeBaseId)) {
    resetHistory = true
    notices.push('原知识库已不可用，请重新选择知识库。')
  } else if (!selectedKnowledgeBaseId) {
    selectedKnowledgeBaseId = defaultKnowledgeBaseId(baseIds)
  }
  if (!baseIds.length) notices.push('暂无可用知识库，请联系管理员。')
  if (selectedCategoryId !== 'all' && !categoryIds.includes(selectedCategoryId)) {
    selectedCategoryId = 'all'; resetHistory = true
    notices.push('原分类已不可用，已切换为自动判断。')
  }
  return { selectedKnowledgeBaseId, selectedCategoryId, resetHistory, notice: notices.join(' ') }
}

export function latestMessageId(messages: ChatMessage[]) {
  return messages.reduce((max, message) => Math.max(max, message.messageId ?? 0), 0)
}

export function buildScopedHistory(messages: ChatMessage[], baseId: string, afterId: number) {
  const result: ChatMessage[] = []
  // Keep complete adjacent turns only. Unknown scope and interrupted turns are boundaries.
  for (let index = messages.length - 1; index >= 1 && result.length < 12; index -= 2) {
    const answer = messages[index]
    const question = messages[index - 1]
    const answerBase = answer.knowledgeBaseId ?? answer.metadata?.agent_route?.knowledge_base_id
    if (answer.role !== 'assistant' || answer.status !== 'complete' || question.role !== 'user' || question.status !== 'complete'
      || !baseId || answerBase !== baseId || (answer.messageId ?? 0) <= afterId
      || (question.messageId !== undefined && question.messageId <= afterId)) break
    result.unshift(question, answer)
  }
  return result.map(({ role, content }) => ({ role, content }))
}
