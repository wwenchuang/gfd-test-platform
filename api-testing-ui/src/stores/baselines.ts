import { defineStore } from 'pinia'

import { apiClient } from '../api/client'
import type {
  ApiBaselineCase,
  BaselineAssertionAuditResponse,
  BaselineAssertionUpgradeResponse,
  BaselineScopeRepairResult,
  CaseVersion,
} from '../api/contracts'

interface BaselineContext {
  projectId: string
  sourceRevisionId?: string
  environmentRevisionId?: string
}

export const useBaselinesStore = defineStore('api-baselines', {
  state: () => ({
    items: [] as ApiBaselineCase[],
    selectedIds: [] as string[],
    loading: false,
    error: '',
    audit: null as BaselineAssertionAuditResponse | null,
    auditProjectId: '',
    auditRequestId: 0,
    auditLoading: false,
    auditError: '',
    creatingUpgradeBaselineId: '',
    scopeRepairPreview: null as BaselineScopeRepairResult | null,
    scopeRepairLoading: false,
  }),
  getters: {
    groups(state): string[] {
      const values = state.items.map(item => baselineGroup(item))
      return [...new Set(values)].sort((a, b) => a.localeCompare(b, 'zh-CN'))
    },
    selectedItems(state): ApiBaselineCase[] {
      const selected = new Set(state.selectedIds)
      return state.items.filter(item => selected.has(item.id))
    },
    selectedEndpointIds(): string[] {
      return [...new Set(this.selectedItems.map(item => item.endpoint_id))]
    },
    auditByBaselineId(state) {
      return new Map((state.audit?.items || []).map(item => [item.baseline_id, item]))
    },
  },
  actions: {
    async load(context: BaselineContext): Promise<void> {
      this.loading = true
      this.error = ''
      this.clearAudit()
      try {
        const query = new URLSearchParams({
          project_id: context.projectId,
        })
        const response = await apiClient.get<{ baselines: ApiBaselineCase[] }>(
          `/api/api-testing/v1/baselines?${query}`,
        )
        this.items = response.data.baselines
        const valid = new Set(this.items.map(item => item.id))
        this.selectedIds = this.selectedIds.filter(item => valid.has(item))
      } catch (error) {
        this.error = error instanceof Error ? error.message : '无法读取基线用例'
      } finally {
        this.loading = false
      }
    },
    clearAudit(): void {
      this.auditRequestId += 1
      this.audit = null
      this.auditProjectId = ''
      this.auditLoading = false
      this.auditError = ''
    },
    async loadAudit(projectId: string): Promise<void> {
      const requestId = this.auditRequestId + 1
      this.auditRequestId = requestId
      this.auditLoading = true
      this.auditError = ''
      try {
        const query = new URLSearchParams({ project_id: projectId })
        const response = await apiClient.get<BaselineAssertionAuditResponse>(
          `/api/api-testing/v1/baselines/assertion-audit?${query}`,
        )
        if (requestId !== this.auditRequestId) return
        this.audit = response.data
        this.auditProjectId = projectId
      } catch (error) {
        if (requestId !== this.auditRequestId) return
        this.audit = null
        this.auditProjectId = ''
        const message = error instanceof Error ? error.message : '未知错误'
        this.auditError = `基线断言检查失败：${message}`
      } finally {
        if (requestId === this.auditRequestId) this.auditLoading = false
      }
    },
    toggle(id: string): void {
      this.scopeRepairPreview = null
      this.selectedIds = this.selectedIds.includes(id)
        ? this.selectedIds.filter(item => item !== id)
        : [...this.selectedIds, id]
    },
    select(ids: string[]): void {
      this.scopeRepairPreview = null
      const valid = new Set(this.items.map(item => item.id))
      this.selectedIds = [...new Set(ids.filter(item => valid.has(item)))]
    },
    clearSelection(): void {
      this.scopeRepairPreview = null
      this.selectedIds = []
    },
    async previewScopeRepair(appPackage: string, business: string): Promise<BaselineScopeRepairResult> {
      this.scopeRepairLoading = true
      this.error = ''
      try {
        const response = await apiClient.post<{ preview: BaselineScopeRepairResult }>(
          '/api/api-testing/v1/baselines/scope-repair/preview',
          { baseline_ids: this.selectedIds, app_package: appPackage, business },
        )
        this.scopeRepairPreview = response.data.preview
        return response.data.preview
      } finally {
        this.scopeRepairLoading = false
      }
    },
    async applyScopeRepair(appPackage: string, business: string): Promise<BaselineScopeRepairResult> {
      this.scopeRepairLoading = true
      this.error = ''
      try {
        const response = await apiClient.post<{ result: BaselineScopeRepairResult }>(
          '/api/api-testing/v1/baselines/scope-repair',
          { baseline_ids: this.selectedIds, app_package: appPackage, business },
        )
        const result = response.data.result
        const updated = new Set(result.items.filter(item => item.status === 'updated').map(item => item.baseline_id))
        this.items = this.items.map(item => updated.has(item.id) ? {
          ...item,
          app_package: result.target.app_package,
          app_name: result.target.app_name,
          business: result.target.business,
        } : item)
        this.scopeRepairPreview = null
        this.clearAudit()
        return result
      } finally {
        this.scopeRepairLoading = false
      }
    },
    async updateGroup(ids: string[], groupName: string): Promise<void> {
      const baselineIds = [...new Set(ids)]
      const nextGroup = groupName.trim()
      if (!baselineIds.length) {
        this.error = '请先选择基线用例'
        return
      }
      if (!nextGroup) {
        this.error = '请输入基线分组名称'
        return
      }
      this.error = ''
      const response = await apiClient.post<{ baselines: Array<{ id: string; group_name: string }> }>(
        '/api/api-testing/v1/baselines/bulk-group',
        { baseline_ids: baselineIds, group_name: nextGroup },
      )
      const updates = new Map(response.data.baselines.map(item => [item.id, item.group_name]))
      this.items = this.items.map(item => {
        const updated = updates.get(item.id)
        return updated ? { ...item, group_name: updated } : item
      })
      this.clearAudit()
    },
    async createAssertionUpgradeDraft(baselineId: string): Promise<CaseVersion> {
      this.creatingUpgradeBaselineId = baselineId
      this.auditError = ''
      try {
        const response = await apiClient.post<BaselineAssertionUpgradeResponse>(
          `/api/api-testing/v1/baselines/${baselineId}/assertion-upgrade-draft`,
          {},
        )
        if (this.audit) {
          this.audit = {
            ...this.audit,
            items: this.audit.items.map(item => item.baseline_id === baselineId
              ? { ...item, upgrade_draft_case_version_id: response.data.case_version.id }
              : item),
          }
        }
        return response.data.case_version
      } finally {
        this.creatingUpgradeBaselineId = ''
      }
    },
    async archive(id: string): Promise<void> {
      this.error = ''
      await apiClient.delete(`/api/api-testing/v1/baselines/${id}`)
      this.items = this.items.filter(item => item.id !== id)
      this.selectedIds = this.selectedIds.filter(item => item !== id)
      this.clearAudit()
    },
  },
})

export function baselineGroup(item: Pick<ApiBaselineCase, 'group_name' | 'tags'>): string {
  return item.group_name?.trim() || item.tags[0] || '未分组'
}
