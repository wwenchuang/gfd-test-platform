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
  workspace: WorkspaceContext
}
