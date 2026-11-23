import { apiGet, apiPost } from './httpClient'
import type { AnswerFeedback, FeedbackRating } from '../types/chat'

export const feedbackRatingLabels = { helpful: '回复优秀', unhelpful: '回复不佳' }
export const feedbackReasons = {
  answer_inaccurate: '回答不准确', incomplete: '回答不完整', off_topic: '没有回答我的问题',
  citation_mismatch: '引用不匹配', response_issue: '响应慢或出错', unclear: '表达不清晰', other: '其他问题',
}
export const reviewStatuses = { pending: '待处理', in_progress: '处理中', resolved: '已解决' }
export type ReviewStatus = keyof typeof reviewStatuses
export type FeedbackRecord = AnswerFeedback & {
  message_id: number; session_id: string; question: string | null; answer: string
  review_status: ReviewStatus; review_note: string; revision: number
}
export interface FeedbackList {
  items: FeedbackRecord[]
  total: number
  counts: Record<FeedbackRating, number>
  review_counts: Record<ReviewStatus, number>
  page: number
  page_size: number
}
export const saveAnswerFeedback = (session: string, message: number, feedback: Omit<AnswerFeedback, 'updated_at'>, signal: AbortSignal) =>
  apiPost<typeof feedback, AnswerFeedback>(`/chat_session/${encodeURIComponent(session)}/messages/${message}/feedback`, feedback, signal)
export const fetchFeedback = (rating: string, page: number, signal: AbortSignal, status = '', pageSize = 10) =>
  apiGet<FeedbackList>(`/answer_feedback?page=${page}&page_size=${pageSize}${rating ? `&rating=${encodeURIComponent(rating)}` : ''}${status ? `&review_status=${encodeURIComponent(status)}` : ''}`, signal)
export const saveFeedbackReview = (message: number, status: ReviewStatus, note: string, revision: number, signal: AbortSignal) =>
  apiPost(`/answer_feedback/${message}/review`, { status, note, expected_revision: revision }, signal)
