/** 与后端 ProcessFields 及流程执行记录协议一致。 */
export interface ProcessFields {
  application_type: string | null
  applicant: string | null
  department: string | null
  permission_level: string | null
  approver: string | null
  additional_info: string | null
}

export interface ProcessExtractionResult {
  fields: ProcessFields
  missing_fields: string[]
  status: 'extracted' | 'failed'
  error: string | null
}

export interface ProcessSubmission {
  request_id: string
  fields: ProcessFields
}

export interface ProcessReceipt {
  request_id: string
  status: 'simulated'
  ticket_id: string
  actions: string[]
}

export interface ProcessExecution {
  request_id: string
  status: 'running' | 'simulated' | 'failed' | 'timeout'
  output: ProcessReceipt | null
  error_message: string | null
  response_status_code: number | null
}
