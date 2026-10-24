import { apiGet, apiPost } from './httpClient'
import type { ProcessExecution, ProcessExtractionResult, ProcessReceipt, ProcessSubmission } from '../types/process'

export function extractApplicationFields(inputText: string) {
  return apiPost<{ input_text: string }, ProcessExtractionResult>('/process_fields/extract', { input_text: inputText })
}

export function submitApplication(request: ProcessSubmission) {
  return apiPost<ProcessSubmission, ProcessReceipt>('/process_applications', request)
}

export function fetchApplicationExecution(requestId: string) {
  return apiGet<ProcessExecution>(`/process_applications/${encodeURIComponent(requestId)}`)
}
