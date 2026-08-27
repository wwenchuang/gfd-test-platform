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
  slug?: string
  description?: string
  status?: string
  created_at?: string
  updated_at?: string
}

export interface SourceRevisionOption {
  id: string
  source_id: string
  project_id: string
  name: string
  source_type?: string
  source_status?: string
  revision_number: number
  endpoint_count: number
  created_at?: string
  activated_at?: string | null
}

export interface EnvironmentRevisionOption {
  id: string
  environment_id: string
  project_id: string
  name: string
  revision: number
  status?: string
  created_at?: string
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
  default_headers?: Record<string, unknown>
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

export interface EnvironmentAsset {
  id: string
  project_id: string
  source_id: string | null
  active_revision_id: string
  source_revision_id: string | null
  revision: number
  name: string
  description: string
  status: 'active' | 'archived'
  service_count: number
  public_variable_count: number
  secret_count: number
  created_at: string
  updated_at: string
}

export interface EnvironmentRevisionSummary {
  id: string
  environment_id: string
  source_revision_id: string | null
  revision: number
  name: string
  description: string
  status: string
  created_at: string
  updated_at: string
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

export interface InlineWorkflowStep {
  name: string
  enabled: boolean
  request: CaseRequest
  assertions: Array<Record<string, unknown>>
  extractions: Array<Record<string, unknown>>
  required_variables: string[]
  polling?: {
    max_attempts: number
    interval_ms: number
  }
}

export interface WorkflowStepPreviewField {
  id: string
  source: 'json_path' | 'header' | 'cookie' | 'status_code'
  path?: string
  name: string
  value: unknown
  value_type: 'null' | 'boolean' | 'number' | 'string' | 'array' | 'object'
  sensitive: boolean
  suggested_target: string
}

export interface WorkflowStepPreview {
  status: string
  failure_category: string
  error_message: string
  trace: DebugTraceStep[]
  response: Record<string, unknown>
  target_index: number
  executed_index: number | null
  target_reached: boolean
  fields: WorkflowStepPreviewField[]
  truncated: boolean
  available_variables: string[]
  missing_variables: string[]
}

export interface CaseDraft {
  name: string
  purpose: string
  business?: string
  priority: 'P0' | 'P1' | 'P2' | 'P3'
  request: CaseRequest
  data_rows: Array<{ name: string; values: Record<string, unknown>; enabled: boolean }>
  assertions: Array<Record<string, unknown>>
  extractions: Array<Record<string, unknown>>
  dependencies: Array<Record<string, unknown>>
  processing: {
    pre: Array<Record<string, unknown>>
    post: Array<Record<string, unknown>>
    setup_steps?: InlineWorkflowStep[]
    cleanup_steps?: InlineWorkflowStep[]
  }
}

export interface CaseVersion extends CaseDraft {
  id: string
  case_id: string
  endpoint_id: string
  status: string
  origin: string
  version: number
  group_name: string
  validation_summary: Record<string, unknown>
}

export interface CaseDependencyOption {
  id: string
  name: string
  group: string
  method: string
  path: string
  version: number
  exports: string[]
}

export interface WorkflowVariableOption {
  name: string
  source: string
  sourceKind: 'environment' | 'dependency' | 'setup' | 'main' | 'unknown'
  available: boolean
}

export interface GeneratedCasePreview {
  id: string
  endpoint_id: string
  origin: string
  workflow?: {
    kind: string
    label: string
    risk: 'low' | 'medium' | 'high' | 'critical'
    requires_setup: boolean
    requires_cleanup: boolean
    baseline_policy: 'direct' | 'guarded' | 'manual' | 'excluded'
    reason: string
  }
  case: CaseDraft
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
  durationMs: number
  errorMessage: string
  trace: DebugTraceStep[]
  resolvedRequest: Record<string, unknown>
  sanitizedResponse: Record<string, unknown>
  assertions: unknown[]
  failureCategory: string
  logs: string[]
}

export interface DebugTraceStep {
  stage: 'setup' | 'main' | 'cleanup'
  index: number
  name: string
  status: string
  failureCategory: string
  assertions: unknown[]
  extractedVariableNames: string[]
  missingVariableNames: string[]
  request: Record<string, unknown>
  response: Record<string, unknown>
  error: string
  attempt: number
  maxAttempts: number
}

export interface ExecutionView {
  id: string
  project_id: string
  task_id?: string | null
  task_name?: string | null
  task_type?: string | null
  execution_source?: string
  state: string
  execution_type: 'debug' | 'regression' | 'baseline_regression' | 'scheduled'
  source_revision_id: string
  environment_revision_id: string
  environment_name: string
  case_statuses: string[]
  case_results: ExecutionCaseResult[]
  summary: Record<string, number>
  notifications?: Record<string, { sent?: boolean; failed?: boolean; message?: string }>
  cancellation_requested: boolean
  created_at: string
  started_at: string | null
  finished_at: string | null
}

export interface ScheduledJob {
  id: string
  project_id: string
  source_revision_id: string | null
  environment_revision_id: string | null
  environment_id: string | null
  name: string
  target_type: 'cases' | 'task' | 'baselines' | 'baseline_group'
  target_ids: string[]
  schedule_type: 'daily' | 'weekly' | 'cron'
  cron_expression: string
  environment_strategy: 'fixed_revision' | 'latest_environment'
  enabled: boolean
  notify_feishu: boolean
  retry_count: number
  timeout_seconds: number
  latest_execution_id: string | null
  effective_cron_expression: string
  scheduler_timezone: string
  scheduler_utc_offset: string
  next_run_at: string | null
  latest_run_at: string | null
  latest_run_trigger: string | null
  latest_execution_state: string | null
  latest_execution_summary: Record<string, number>
  created_at: string
  updated_at: string
}

export interface ExecutionCaseResult {
  execution_case_id: string
  case_version_id: string
  endpoint_id: string
  execution_role?: 'requested' | 'dependency'
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
  createdAt?: string
  message: string
  payload: Record<string, unknown>
}

export type ApiTestTaskState = 'draft' | 'designing' | 'debugging' | 'ready' | 'running' | 'failed' | 'completed'

export interface ApiTestTask {
  id: string
  project_id: string
  source_revision_id: string
  environment_revision_id: string
  name: string
  state: ApiTestTaskState
  selected_endpoint_ids: string[]
  runnable_baseline_count: number
  runnable_endpoint_count?: number
  latest_ai_job_id: string | null
  latest_execution_id: string | null
  latest_execution_state?: string | null
  latest_execution_summary?: Record<string, number>
  latest_execution_at?: string | null
  summary: Record<string, unknown>
  created_at: string
  updated_at: string
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

export interface ApiBaselineCase {
  id: string
  project_id: string
  case_id: string
  case_version_id: string
  environment_revision_id: string
  source_revision_id: string
  endpoint_id: string
  status: string
  case_name: string
  case_version: number
  priority: string
  business?: string
  origin: string
  method: string
  path: string
  endpoint_summary: string
  tags: string[]
  group_name: string
  adoption_reason: string
  adopted_at: string
}

export interface FeishuNotification {
  project_id: string
  channel_type: 'feishu'
  name: string
  enabled: boolean
  configured: boolean
  fingerprint: string
  updated_at: string | null
}

export interface NotificationSendResult {
  execution_id: string
  channel_type: 'feishu'
  sent: boolean
  message: string
}

export interface NotificationTestResult {
  project_id: string
  channel_type: 'feishu'
  sent: boolean
  message: string
}
