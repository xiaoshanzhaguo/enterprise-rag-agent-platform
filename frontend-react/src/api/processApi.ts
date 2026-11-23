import { apiDelete, apiGet, apiPatch, apiPost } from './httpClient'
import type { ProcessExecution, ProcessExecutionPage, ProcessSubmission } from '../types/process'

export interface ProcessTicket {
  request_id: string
  ticket_number: string
  fields: ProcessSubmission['fields']
  status: 'pending' | 'approved' | 'rejected'
  approver_name: string
  decision_note: string | null
  created_at: string
  decided_at: string | null
  can_decide: boolean
}

export const ticketStatusLabels = { pending: '待审批', approved: '已通过', rejected: '已驳回' }
interface ApprovalRoute { application_type: string; approver_id: number; approver_name: string }
export interface ApprovalRoutes { department: string; routes: ApprovalRoute[] }
type ApprovalRuleInput = { department_id: number; application_type: string; approver_id: number }
export interface ApprovalRule { id: number; department: string; application_type: string; approver_id: number; approver_name: string; approver_enabled: boolean }
export interface TicketEvent {
  event_id: string
  event_type: 'process_ticket.submitted' | 'process_ticket.approved' | 'process_ticket.rejected'
  ticket_number: string
  status: 'pending' | 'sending' | 'sent' | 'failed'
  attempts: number
  error_message: string | null
}

export const getApprovalRoutes = (signal: AbortSignal) => apiGet<ApprovalRoutes>('/process_ticket_routes', signal)
export const listApprovalRules = () => apiGet<ApprovalRule[]>('/process_approval_rules')
export const saveApprovalRule = (data: ApprovalRuleInput) => apiPost<ApprovalRuleInput, { saved: boolean }>('/process_approval_rules', data, AbortSignal.timeout(15000))
export const updateApprovalRule = (id: number, approver_id: number) => apiPatch<{ saved: boolean }>(`/process_approval_rules/${id}`, { approver_id })
export const deleteApprovalRule = (id: number) => apiDelete<{ deleted: boolean }>(`/process_approval_rules/${id}`)
export const listTicketEvents = () => apiGet<TicketEvent[]>('/process_ticket_events')
export const retryTicketEvent = (id: string, expected_attempts: number) => apiPost<{ expected_attempts: number }, { queued: boolean }>(
  `/process_ticket_events/${encodeURIComponent(id)}/retry`, { expected_attempts }, AbortSignal.timeout(15000),
)
export const createTicket = (data: ProcessSubmission & { expected_approver_id: number; expected_department: string }) =>
  apiPost<typeof data, ProcessTicket>('/process_tickets', data, AbortSignal.timeout(15000))
export const getTicket = (id: string) => apiGet<ProcessTicket>(`/process_tickets/${encodeURIComponent(id)}`)
export const listTickets = (view: 'mine' | 'assigned') => apiGet<ProcessTicket[]>(`/process_tickets?view=${view}`)
export const getPendingApprovalCount = (signal: AbortSignal) =>
  apiGet<{ pending_count: number }>('/process_tickets/pending-count', signal)
export const decideTicket = (id: string, decision: 'approved' | 'rejected', note: string) =>
  apiPost<{ decision: typeof decision; note: string }, ProcessTicket>(
    `/process_tickets/${encodeURIComponent(id)}/decision`, { decision, note }, AbortSignal.timeout(15000),
  )

export function fetchApplicationExecution(requestId: string, signal?: AbortSignal) {
  return apiGet<ProcessExecution>(`/process_applications/${encodeURIComponent(requestId)}`, signal)
}

export function fetchApplicationExecutions(query: URLSearchParams, signal?: AbortSignal) {
  return apiGet<ProcessExecutionPage>(`/process_applications?${query}`, signal)
}
