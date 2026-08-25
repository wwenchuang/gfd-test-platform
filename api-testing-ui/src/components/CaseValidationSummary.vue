<script setup lang="ts">
import { computed } from 'vue'
import { AlertCircle, AlertTriangle, CheckCircle2 } from 'lucide-vue-next'

const props = defineProps<{
  setupCount: number
  assertionCount: number
  cleanupCount: number
  errors: Record<string, string>
  warnings: Record<string, string>
}>()
const emit = defineEmits<{ navigate: [fieldPath: string] }>()
const issues = computed(() => [
  ...Object.entries(props.errors).map(([path, message]) => ({ path, message, level: 'error' as const })),
  ...Object.entries(props.warnings).map(([path, message]) => ({ path, message, level: 'warning' as const })),
])

function location(path: string): string {
  const setup = path.match(/^processing\.setup_steps\[(\d+)]/)
  if (setup) return `前置 ${Number(setup[1]) + 1}`
  const cleanup = path.match(/^processing\.cleanup_steps\[(\d+)]/)
  if (cleanup) return `清理 ${Number(cleanup[1]) + 1}`
  const assertion = path.match(/^assertions\[(\d+)]/)
  if (assertion) return `断言 ${Number(assertion[1]) + 1}`
  const extraction = path.match(/^extractions\[(\d+)]/)
  if (extraction) return `提取 ${Number(extraction[1]) + 1}`
  if (path.startsWith('dependencies')) return '共享前置用例'
  if (path.startsWith('data_rows')) return '测试数据'
  return '主体请求'
}
</script>

<template>
  <section class="case-validation-summary" :class="{ clean: !issues.length }">
    <header>
      <div class="validation-state">
        <CheckCircle2 v-if="!issues.length" :size="17" />
        <AlertCircle v-else-if="Object.keys(errors).length" :size="17" />
        <AlertTriangle v-else :size="17" />
        <span>{{ issues.length ? '保存前检查' : '用例结构完整' }}</span>
      </div>
      <div class="validation-counts"><span>前置 {{ setupCount }}</span><span>断言 {{ assertionCount }}</span><span>清理 {{ cleanupCount }}</span><b v-if="Object.keys(errors).length">错误 {{ Object.keys(errors).length }}</b><i v-if="Object.keys(warnings).length">警告 {{ Object.keys(warnings).length }}</i></div>
    </header>
    <details v-if="issues.length">
      <summary>查看并定位 {{ issues.length }} 个问题</summary>
      <div class="validation-issues">
        <button v-for="(issue, index) in issues" :key="`${issue.level}-${issue.path}`" :data-testid="`validation-issue-${index}`" type="button" :class="issue.level" @click="emit('navigate', issue.path)">
          <span>{{ location(issue.path) }}</span><strong>{{ issue.message }}</strong><small>定位</small>
        </button>
      </div>
    </details>
  </section>
</template>
