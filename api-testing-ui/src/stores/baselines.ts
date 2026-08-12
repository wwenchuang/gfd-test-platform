import { defineStore } from 'pinia'

import { apiClient } from '../api/client'
import type { ApiBaselineCase } from '../api/contracts'

interface BaselineContext {
  projectId: string
  sourceRevisionId: string
  environmentRevisionId: string
}

export const useBaselinesStore = defineStore('api-baselines', {
  state: () => ({
    items: [] as ApiBaselineCase[],
    selectedIds: [] as string[],
    loading: false,
    error: '',
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
  },
  actions: {
    async load(context: BaselineContext): Promise<void> {
      this.loading = true
      this.error = ''
      try {
        const query = new URLSearchParams({
          project_id: context.projectId,
          source_revision_id: context.sourceRevisionId,
          environment_revision_id: context.environmentRevisionId,
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
    toggle(id: string): void {
      this.selectedIds = this.selectedIds.includes(id)
        ? this.selectedIds.filter(item => item !== id)
        : [...this.selectedIds, id]
    },
    select(ids: string[]): void {
      const valid = new Set(this.items.map(item => item.id))
      this.selectedIds = [...new Set(ids.filter(item => valid.has(item)))]
    },
    clearSelection(): void {
      this.selectedIds = []
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
    },
    async archive(id: string): Promise<void> {
      this.error = ''
      await apiClient.delete(`/api/api-testing/v1/baselines/${id}`)
      this.items = this.items.filter(item => item.id !== id)
      this.selectedIds = this.selectedIds.filter(item => item !== id)
    },
  },
})

export function baselineGroup(item: Pick<ApiBaselineCase, 'group_name' | 'tags'>): string {
  return item.group_name?.trim() || item.tags[0] || '未分组'
}
