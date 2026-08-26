import type { ApiTestTask } from '../api/contracts'
import { formatPassRate } from './executionPresentation'

const TASK_STATE_LABELS: Record<string, string> = {
  draft: '待设计',
  designing: 'AI生成中',
  debugging: '调试中',
  ready: '可执行',
  running: '执行中',
  failed: '需处理',
  completed: '已完成',
}

export function taskStateLabel(state: string): string {
  return TASK_STATE_LABELS[state] || state || '未知'
}

export function taskLatestResult(task: ApiTestTask): string {
  if (!task.latest_execution_id) return '尚未执行'
  const summary = task.latest_execution_summary || {}
  const total = numberValue(summary.total)
  const passed = numberValue(summary.passed)
  if (!total) return `最近执行 ${task.latest_execution_state ? executionStateLabel(task.latest_execution_state) : '已记录'}`
  return `最近结果 通过 ${passed}/${total} · ${formatPassRate(passed, total)}`
}

export function compactDateTime(value: string | null | undefined): string {
  if (!value) return '未知时间'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return '未知时间'
  return date.toLocaleString('zh-CN', {
    month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false,
  })
}

function numberValue(value: unknown): number {
  const number = Number(value)
  return Number.isFinite(number) ? Math.max(0, number) : 0
}

function executionStateLabel(state: string): string {
  const labels: Record<string, string> = {
    QUEUED: '等待中', RUNNING: '执行中', DONE: '已结束', CANCELLED: '已取消',
  }
  return labels[state.toUpperCase()] || '已记录'
}
