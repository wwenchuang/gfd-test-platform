import { defineStore } from 'pinia'

import { apiClient } from '../api/client'
import type {
  LoadAgent, LoadAgentEnrollmentResult, LoadAiAnalysis, LoadReport, LoadRun,
  LoadScenario, LoadScenarioDefinition, LoadScenarioVersion,
} from '../api/contracts'

export const useLoadTestingStore = defineStore('api-load-testing', {
  state: () => ({
    agents: [] as LoadAgent[],
    scenarios: [] as LoadScenario[],
    runs: [] as LoadRun[],
    loadingAgents: false,
    loadingScenarios: false,
    loadingRuns: false,
    mutating: false,
    agentError: '',
    scenarioError: '',
    runError: '',
  }),
  actions: {
    async loadAgents(): Promise<LoadAgent[]> {
      this.loadingAgents = true
      this.agentError = ''
      try {
        const response = await apiClient.get<{ agents: LoadAgent[] }>('/api/api-testing/v1/load-agents')
        this.agents = response.data.agents
        return this.agents
      } catch (error) {
        this.agentError = message(error, '无法读取压测节点')
        return []
      } finally {
        this.loadingAgents = false
      }
    },
    async createEnrollment(input: { name: string; node_group: string; scheduling_tier: string; expires_in_seconds: number }): Promise<LoadAgentEnrollmentResult> {
      this.mutating = true
      this.agentError = ''
      try {
        const response = await apiClient.post<{ enrollment: LoadAgentEnrollmentResult }>('/api/api-testing/v1/load-agent-enrollments', input)
        return response.data.enrollment
      } catch (error) {
        this.agentError = message(error, '节点注册令牌创建失败')
        throw error
      } finally {
        this.mutating = false
      }
    },
    async updateAgent(agentId: string, input: Record<string, unknown>): Promise<LoadAgent> {
      this.mutating = true
      this.agentError = ''
      try {
        const response = await apiClient.put<{ agent: LoadAgent }>(`/api/api-testing/v1/load-agents/${encodeURIComponent(agentId)}`, input)
        this.replaceAgent(response.data.agent)
        return response.data.agent
      } catch (error) {
        this.agentError = message(error, '节点配置保存失败')
        throw error
      } finally {
        this.mutating = false
      }
    },
    async calibrateAgent(agentId: string): Promise<LoadAgent> {
      this.mutating = true
      this.agentError = ''
      try {
        const response = await apiClient.post<{ agent: LoadAgent }>(`/api/api-testing/v1/load-agents/${encodeURIComponent(agentId)}/calibrate`, {})
        this.replaceAgent(response.data.agent)
        return response.data.agent
      } catch (error) {
        this.agentError = message(error, '节点校准请求失败')
        throw error
      } finally {
        this.mutating = false
      }
    },
    async loadScenarios(projectId: string): Promise<LoadScenario[]> {
      this.loadingScenarios = true
      this.scenarioError = ''
      try {
        const response = await apiClient.get<{ scenarios: LoadScenario[] }>(`/api/api-testing/v1/load-scenarios?project_id=${encodeURIComponent(projectId)}`)
        this.scenarios = response.data.scenarios
        return this.scenarios
      } catch (error) {
        this.scenarioError = message(error, '无法读取性能场景')
        return []
      } finally { this.loadingScenarios = false }
    },
    async createScenario(input: { project_id: string; name: string; description: string; scenario_type: string }): Promise<LoadScenario> {
      const response = await apiClient.post<{ scenario: LoadScenario }>('/api/api-testing/v1/load-scenarios', input)
      this.scenarios = [response.data.scenario, ...this.scenarios]
      return response.data.scenario
    },
    async saveScenarioVersion(scenarioId: string, definition: LoadScenarioDefinition): Promise<LoadScenarioVersion> {
      const response = await apiClient.post<{ version: LoadScenarioVersion }>(`/api/api-testing/v1/load-scenarios/${encodeURIComponent(scenarioId)}/versions`, { definition })
      await this.loadScenarios(this.scenarios.find(item => item.id === scenarioId)?.project_id || '')
      return response.data.version
    },
    async archiveScenario(scenarioId: string): Promise<void> {
      await apiClient.delete(`/api/api-testing/v1/load-scenarios/${encodeURIComponent(scenarioId)}`)
      this.scenarios = this.scenarios.filter(item => item.id !== scenarioId)
    },
    async loadRuns(projectId: string): Promise<LoadRun[]> {
      this.loadingRuns = true
      this.runError = ''
      try {
        const response = await apiClient.get<{ runs: LoadRun[] }>(`/api/api-testing/v1/load-runs?project_id=${encodeURIComponent(projectId)}`)
        this.runs = response.data.runs
        return this.runs
      } catch (error) {
        this.runError = message(error, '无法读取压测执行')
        return []
      } finally { this.loadingRuns = false }
    },
    async createRun(input: Record<string, unknown>): Promise<LoadRun> {
      const response = await apiClient.post<{ run: LoadRun }>('/api/api-testing/v1/load-runs', input)
      this.replaceRun(response.data.run)
      return response.data.run
    },
    async preflightRun(runId: string): Promise<LoadRun> { return this.runAction(runId, 'preflight') },
    async prepareConnectivity(runId: string): Promise<LoadAgent[]> {
      const response = await apiClient.post<{ agents: LoadAgent[] }>(`/api/api-testing/v1/load-runs/${encodeURIComponent(runId)}/connectivity`, {})
      for (const agent of response.data.agents) this.replaceAgent(agent)
      return response.data.agents
    },
    async startRun(runId: string): Promise<LoadRun> { return this.runAction(runId, 'start') },
    async stopRun(runId: string, reason = '用户在页面停止'): Promise<LoadRun> { return this.runAction(runId, 'stop', { reason }) },
    async loadReport(runId: string): Promise<LoadReport> {
      return (await apiClient.get<{ report: LoadReport }>(`/api/api-testing/v1/load-runs/${encodeURIComponent(runId)}/report`)).data.report
    },
    async loadAiAnalysis(runId: string): Promise<LoadAiAnalysis | null> {
      return (await apiClient.get<{ analysis: LoadAiAnalysis | null }>(`/api/api-testing/v1/load-runs/${encodeURIComponent(runId)}/ai-analysis`)).data.analysis
    },
    async requestAiAnalysis(runId: string, force = false): Promise<LoadAiAnalysis> {
      return (await apiClient.post<{ analysis: LoadAiAnalysis }>(`/api/api-testing/v1/load-runs/${encodeURIComponent(runId)}/ai-analysis`, { force })).data.analysis
    },
    replaceAgent(agent: LoadAgent): void {
      this.agents = this.agents.some(item => item.id === agent.id)
        ? this.agents.map(item => item.id === agent.id ? agent : item) : [agent, ...this.agents]
    },
    replaceRun(run: LoadRun): void {
      this.runs = this.runs.some(item => item.id === run.id)
        ? this.runs.map(item => item.id === run.id ? run : item) : [run, ...this.runs]
    },
    async runAction(runId: string, action: 'preflight' | 'start' | 'stop', body: Record<string, unknown> = {}): Promise<LoadRun> {
      const response = await apiClient.post<{ run: LoadRun }>(`/api/api-testing/v1/load-runs/${encodeURIComponent(runId)}/${action}`, body)
      this.replaceRun(response.data.run)
      return response.data.run
    },
  },
})

function message(error: unknown, fallback: string): string {
  return error instanceof Error ? error.message : fallback
}
