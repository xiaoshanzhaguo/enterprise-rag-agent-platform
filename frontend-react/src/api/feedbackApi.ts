import { apiGet, apiPost } from './httpClient'
import type { AnswerFeedback, FeedbackRating } from '../types/chat'

export const feedbackReasons = {
  answer_inaccurate: '答案不准确', citation_mismatch: '引用不匹配', incomplete: '回答不完整', other: '其他问题',
}
export interface FeedbackList {
  items: Array<AnswerFeedback & { message_id: number; session_id: string; question: string | null; answer: string }>
  total: number
  counts: Record<FeedbackRating, number>
  page: number
  page_size: number
}
export const saveAnswerFeedback = (session: string, message: number, feedback: Omit<AnswerFeedback, 'updated_at'>, signal: AbortSignal) =>
  apiPost<typeof feedback, AnswerFeedback>(`/chat_session/${encodeURIComponent(session)}/messages/${message}/feedback`, feedback, signal)
export const fetchFeedback = (rating: string, page: number, signal: AbortSignal) =>
  apiGet<FeedbackList>(`/answer_feedback?page=${page}&page_size=10${rating ? `&rating=${encodeURIComponent(rating)}` : ''}`, signal)
