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

export interface ProjectOption {
  id: string
  name: string
}

export interface SourceRevisionOption {
  id: string
  source_id: string
  project_id: string
  name: string
  revision_number: number
  endpoint_count: number
}

export interface EnvironmentRevisionOption {
  id: string
  environment_id: string
  project_id: string
  name: string
  revision: number
}

export interface EnvironmentServiceSnapshot {
  name: string
  module_name?: string
  base_url: string | null
  unresolved: boolean
  metadata?: Record<string, unknown>
}

export interface EnvironmentRevisionSnapshot {
  revision_id: string
  variables: Record<string, unknown>
  services: Record<string, EnvironmentServiceSnapshot>
}

export interface ProviderCredential {
  provider: 'apifox'
  configured: boolean
  fingerprint: string
  updated_at: string | null
}

export interface ApifoxProject {
  id: string
  name: string
  description: string
  team_name: string
}

export interface ApifoxBranch {
  id: string
  name: string
  is_default: boolean
}

export interface ApifoxEnvironment {
  id: string
  name: string
  services: Array<Record<string, unknown>>
  variables: Array<Record<string, unknown>>
}

export interface ApifoxProjectContext {
  project: ApifoxProject
  branches: ApifoxBranch[]
  environments: ApifoxEnvironment[]
  cli_version: string
}

export interface ApifoxRefreshPreview {
  source_preview: SourcePreview
  environment_candidate: {
    name: string
    services?: Array<Record<string, unknown>>
    variables?: Record<string, unknown>
    secret_placeholders: string[]
  }
}

export interface ApifoxActivation {
  source_revision: SourceRevision
  environment: EnvironmentView
  workspace: WorkspaceContext
  secret_placeholders: string[]
}

export interface SourcePreview {
  id: string
  project_id: string
  source_id: string
  previous_revision_id: string | null
  candidate_revision_id: string
  added_count: number
  changed_count: number
  removed_count: number
  changes: Array<Record<string, unknown>>
}

export interface SourceRevision {
  id: string
  project_id: string
  source_id: string
  revision_number: number
  status: string
  normalized_document: Record<string, unknown>
  endpoints: ApiEndpoint[]
}

export interface EnvironmentView {
  id: string
  project_id: string
  source_id: string | null
  revision_id: string
  source_revision_id: string | null
  revision: number
  name: string
  description: string
  status: string
  services: Record<string, EnvironmentServiceSnapshot>
  variables: Record<string, unknown>
  default_headers: Record<string, unknown>
}

export interface ContextOptionsResponse {
  projects: ProjectOption[]
  source_revisions: SourceRevisionOption[]
  environment_revisions: EnvironmentRevisionOption[]
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
  project_id: string
  state: string
  execution_type: 'debug' | 'regression'
  source_revision_id: string
  environment_revision_id: string
  environment_name: string
  case_statuses: string[]
  case_results: ExecutionCaseResult[]
  summary: Record<string, number>
  cancellation_requested: boolean
  created_at: string
  started_at: string | null
  finished_at: string | null
}

export interface ExecutionCaseResult {
  execution_case_id: string
  case_version_id: string
  endpoint_id: string
  case_name: string
  endpoint_summary: string
  method: string
  path: string
  status: string
  failure_category: string
  duration_ms: number
  sanitized_result: Record<string, unknown>
  failure_analysis?: {
    category: string
    analyzer: string
    model: string
    analysis: {
      summary?: string
      root_cause?: string
      recommendations?: string[]
      evidence?: string[]
      model_evidence?: Record<string, unknown>
    }
  } | null
}

export type ExecutionConnectionState = 'idle' | 'connecting' | 'open' | 'reconnecting' | 'complete' | 'failed'

export interface ExecutionEventView {
  id: number
  type: string
  level: 'info' | 'warning' | 'error' | 'success'
  caseId: string
  message: string
  payload: Record<string, unknown>
}

export interface CaseValidationIssue {
  code: string
  field: string
  message: string
}

export interface CaseValidation {
  valid: boolean
  errors: CaseValidationIssue[]
  warnings: CaseValidationIssue[]
}
