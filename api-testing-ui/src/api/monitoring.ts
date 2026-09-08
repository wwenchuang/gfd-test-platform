import { apiClient } from './client'

export type MonitoringDeployment = 'host' | 'container' | 'pod' | 'pod_state' | 'postgres'
export type MonitoringCheck = { state: 'ready' | 'missing' | 'failed'; message: string; checked_at?: string }
export type MonitoringService = {
  id: string; revision_id: string; environment_id: string; name: string; description: string
  deployment?: MonitoringDeployment; status: string; source_url: string; labels: Record<string, string>; metrics: string[]
  step_seconds: number; has_token: boolean; last_check?: MonitoringCheck | null
}
export type MonitoringPayload = {
  environment_revision_id: string; name: string; description: string; deployment: MonitoringDeployment
  source_url: string; labels: Record<string, string>; metrics: string[]; step_seconds: number
  token?: string; authorize_host?: boolean
}
export type MonitoringSelection = { services: Array<{ revision_id: string; required: boolean }>; before_seconds: number; after_seconds: number }
const base = '/api/api-testing/v1/load-monitoring-services'
export const monitoringApi = {
  async list(environmentRevisionId: string): Promise<MonitoringService[]> {
    return (await apiClient.get<{ items: MonitoringService[] }>(`${base}?environment_revision_id=${encodeURIComponent(environmentRevisionId)}`)).data.items
  },
  async save(id: string | undefined, payload: MonitoringPayload): Promise<MonitoringService> {
    const response = id ? await apiClient.put<{ service: MonitoringService }>(`${base}/${encodeURIComponent(id)}`, payload) : await apiClient.post<{ service: MonitoringService }>(base, payload)
    return response.data.service
  },
  async check(id: string, revisionId?: string): Promise<MonitoringCheck> {
    return (await apiClient.post<MonitoringCheck>(`${base}/${encodeURIComponent(id)}/check`, revisionId ? { revision_id: revisionId } : {})).data
  },
  async disable(id: string): Promise<void> { await apiClient.post(`${base}/${encodeURIComponent(id)}/disable`, {}) },
}
