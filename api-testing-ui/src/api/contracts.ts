export interface WorkspaceContext {
  project_id: string | null
  source_revision_id: string | null
  environment_revision_id: string | null
}

export interface ApiEnvelope<T> {
  data: T
  request_id?: string
}

export interface WorkspaceResponse {
  workspace: WorkspaceContext | null
}

export type LoadState = 'idle' | 'loading' | 'ready' | 'partial' | 'empty' | 'failed'

export interface ApiEndpoint {
  id: string
  revision_id?: string
  operation_id?: string
  method: string
  path: string
  summary: string
  tags: string[]
  operation?: Record<string, unknown>
}

export interface CaseRequest {
  method: string
  path: string
  service: string
  path_params: Record<string, unknown>
  query: Record<string, unknown>
  headers: Record<string, unknown>
  cookies: Record<string, unknown>
  body: unknown
}

export interface CaseDraft {
  name: string
  purpose: string
  priority: 'P0' | 'P1' | 'P2' | 'P3'
  request: CaseRequest
  data_rows: Array<{ name: string; values: Record<string, unknown>; enabled: boolean }>
  assertions: Array<Record<string, unknown>>
  extractions: Array<Record<string, unknown>>
  dependencies: Array<Record<string, unknown>>
  processing: { pre: Array<Record<string, unknown>>; post: Array<Record<string, unknown>> }
}

export interface CaseVersion extends CaseDraft {
  id: string
  case_id: string
  endpoint_id: string
  status: string
  origin: string
  version: number
  validation_summary: Record<string, unknown>
}

export type AiJobState = 'queued' | 'running' | 'completed' | 'partial' | 'failed' | 'failed_gateway' | 'failed_validation'

export interface AiJobBatch {
  id: string
  sequence: number
  state: AiJobState
  endpoint_ids: string[]
  requested_model: string
  actual_model: string
  fallback_used: boolean
  fallback_reason: string
  generated_draft_ids: string[]
  validation_errors: Array<Record<string, unknown>>
}

export interface AiJob {
  id: string
  state: AiJobState
  endpoint_ids: string[]
  requested_model: string
  actual_model: string
  fallback_used: boolean
  summary: Record<string, unknown>
  batches: AiJobBatch[]
}

export interface DebugResult {
  status: string
  executionCaseId: string
  resolvedRequest: Record<string, unknown>
  sanitizedResponse: Record<string, unknown>
  assertions: unknown[]
  failureCategory: string
  logs: string[]
}

export interface ExecutionView {
  id: string
  state: string
  case_statuses: string[]
  case_results: Array<{
    execution_case_id: string
    case_version_id: string
    endpoint_id: string
    status: string
    failure_category: string
    duration_ms: number
    sanitized_result: Record<string, unknown>
  }>
  summary: Record<string, number>
}
