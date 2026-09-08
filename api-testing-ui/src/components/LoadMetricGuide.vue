<script setup lang="ts">
import { computed, ref, useId } from 'vue'

const props = defineProps<{ initiallyExpanded?: boolean }>()
const expanded = ref(Boolean(props.initiallyExpanded))
const query = ref('')
const composing = ref(false)
const contentId = useId()
const entries = [
  {
    title: '维度与指标',
    meaning: '维度决定按什么分组，指标表示每组测到了什么。常用维度有全程、时间窗口、接口、步骤、业务链路和节点。',
    formula: '选定同一范围 → 按维度分组 → 计算该组指标',
    example: '按接口分组后，比较“登录”和“查询”的请求数与响应时间。',
    caveat: '先核对时间范围和样本口径。一个请求可能同时属于接口、步骤和节点，不能把这些维度的总数再次相加。',
  },
  {
    title: 'VU · 虚拟用户 / 并发',
    meaning: 'VU 是并行执行业务流程的虚拟用户数，RPS 是每秒 HTTP 请求数，两者不是同一指标。',
    formula: '固定 VU 稳态下，RPS 约等于 VU × 每轮请求数 ÷ 每轮秒数',
    example: '10 个 VU，每轮 1 个请求、含等待共耗时 2 秒，约产生 5 RPS。',
    caveat: '这是粗略估算；响应变慢、思考时间和业务分支都会改变结果。到达率模式控制每秒开始的迭代数，也不直接等于 HTTP 请求数。',
  },
  {
    title: 'Samples / KO · 样本数与失败数',
    meaning: 'Samples 是统计范围内的样本数量；KO 是其中失败样本数量。HTTP 表通常按请求计数，事务表按业务链路计数。',
    formula: '失败率 = KO ÷ Samples × 100%',
    example: '1,000 个 HTTP 样本有 20 个失败，HTTP 失败率为 2%。',
    caveat: '先看表头声明的样本单位和失败规则。断言执行次数不等于请求数；Samples 为 0 时失败率无定义，不能当作 0%。',
  },
  {
    title: 'RPS 与事务 TPS · 吞吐量',
    meaning: 'RPS 衡量 HTTP 请求吞吐；事务 TPS 衡量完整业务链路的吞吐。成功事务 TPS 还要求链路成功。',
    formula: 'RPS = 请求数 ÷ 统计秒数；事务 TPS = 完成事务数 ÷ 统计秒数',
    example: '60 秒完成 100 次链路，每次含 3 个请求：约 5 RPS、1.67 事务 TPS。',
    caveat: '重试、失败中断和分支可能改变每次事务的请求数。比较时应使用相同时间范围；目标到达率不是实际完成的 TPS。',
  },
  {
    title: 'HTTP 错误与业务失败',
    meaning: 'HTTP 错误按请求成功规则判断；业务失败由业务断言或链路规则判断。HTTP 200 仍可能返回业务错误码。',
    formula: 'HTTP 错误率 = HTTP 失败请求数 ÷ 请求数；断言失败率 = 失败断言数 ÷ 已执行断言数',
    example: '接口返回 HTTP 200，但 code=1001 不符合业务断言：业务失败，HTTP 层可能成功。',
    caveat: '两类失败可能重叠，不能直接相加。未配置或未执行断言，不代表业务成功；链路失败率需以链路样本为分母。',
  },
  {
    title: '响应时间 · 均值与 P50 / P90 / P95 / P99',
    meaning: '均值反映平均耗时；分位数观察慢请求尾部。P95 为 300 毫秒，表示约 95% 的样本耗时不超过 300 毫秒。',
    formula: '总均值 = 各组耗时总和 ÷ 总样本数 = Σ(组均值 × 组样本数) ÷ Σ组样本数',
    example: '10 个请求均值 100 毫秒，90 个请求均值 200 毫秒，总均值是 190 毫秒。',
    caveat: '不能平均各节点或各时段的 P95；全程分位数需合并原始样本或可合并分布后计算。分桶估算有精度限制，少量样本的高分位不稳定。',
  },
  {
    title: '资源 · 压测 Agent 与被测服务',
    meaning: '压测 Agent 的 CPU、内存反映发压端状态。被测服务的 CPU、内存、数据库等需要从目标系统另外采集。',
    formula: '资源占用率 = 已使用资源 ÷ 对应资源限额 × 100%',
    example: 'Agent CPU 持续接近容器配额，可能限制实际发出的负载，不能据此认定被测服务 CPU 已满。',
    caveat: '核对宿主机、容器配额及采样时间等口径。只采集到 Agent 资源时，不能推断目标服务资源情况或瓶颈。',
  },
  {
    title: '— / 不可用 · 证据缺失',
    meaning: '不可用表示未采集、无样本或证据不完整；0 表示已采集且结果确实为零。',
    formula: '没有有效分母或必要样本 → 不可计算 → 显示“—”',
    example: '没有收到响应时间样本，显示“—”；有 100 个请求且确认没有 HTTP 错误，错误率才是 0%。',
    caveat: '零请求不能证明性能通过。节点丢失或窗口缺失时，应先补齐证据，再判断是否达到目标。',
  },
]
const filteredEntries = computed(() => {
  const term = query.value.trim().toLocaleLowerCase()
  return entries.filter(entry => Object.values(entry).join(' ').toLocaleLowerCase().includes(term))
})
function updateQuery(event: Event) {
  if (!composing.value && !(event as InputEvent).isComposing) {
    query.value = (event.target as HTMLInputElement).value
  }
}
function finishComposition(event: CompositionEvent) {
  composing.value = false
  query.value = (event.target as HTMLInputElement).value
}
</script>

<template>
  <section class="load-metric-guide" aria-label="性能指标词典">
    <button class="guide-toggle" type="button" :aria-expanded="expanded" :aria-controls="contentId" @click="expanded = !expanded">
      <span><strong>指标词典</strong><span class="guide-subtitle">读懂统计口径与计算方式</span></span>
      <span class="guide-toggle-label">{{ expanded ? '收起' : '展开' }} <span aria-hidden="true">{{ expanded ? '−' : '+' }}</span></span>
    </button>
    <div v-if="expanded" :id="contentId" class="guide-content">
      <label class="guide-search">查找指标或维度
        <input type="search" :value="query" placeholder="例如 VU、P95、失败、资源" @input="updateQuery" @compositionstart="composing = true" @compositionend="finishComposition" />
      </label>
      <p v-if="!filteredEntries.length" role="status" class="guide-empty">没有匹配的指标，请尝试其他关键词。</p>
      <div class="guide-grid">
        <article v-for="entry in filteredEntries" :key="entry.title">
          <h3>{{ entry.title }}</h3>
          <p>{{ entry.meaning }}</p>
          <p class="guide-formula"><strong>计算口径</strong>{{ entry.formula }}</p>
          <p><strong>例子：</strong>{{ entry.example }}</p>
          <p class="guide-caveat"><strong>注意：</strong>{{ entry.caveat }}</p>
        </article>
      </div>
    </div>
  </section>
</template>

<style scoped>
.load-metric-guide { border: 1px solid #d7e0e8; border-radius: 12px; background: #fff; color: #243449; min-width: 0; }
.guide-toggle { width: 100%; display: flex; align-items: center; justify-content: space-between; gap: 12px; padding: 16px 18px; background: transparent; border: 0; border-radius: 12px; color: inherit; text-align: left; cursor: pointer; font: inherit; }
.guide-toggle strong { font-size: 16px; }
.guide-subtitle { display: inline-block; margin-left: 12px; font-size: 13px; color: #52647a; }
.guide-toggle-label { flex-shrink: 0; color: #365b83; font-size: 13px; }
.guide-content { padding: 0 18px 18px; }
.guide-search { display: grid; gap: 7px; font-size: 13px; font-weight: 600; max-width: 420px; margin: 2px 0 16px; }
.guide-search input { box-sizing: border-box; width: 100%; min-width: 0; border: 1px solid #adbdce; border-radius: 7px; padding: 10px 12px; font: inherit; background: #fff; color: #243449; }
.guide-toggle:focus-visible, .guide-search input:focus-visible { outline: 3px solid #4886bb; outline-offset: 2px; }
.guide-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(100%, 310px), 1fr)); gap: 12px; }
.guide-grid article { min-width: 0; border: 1px solid #e2e8ef; border-radius: 9px; padding: 14px; overflow-wrap: anywhere; }
.guide-grid h3 { font-size: 15px; line-height: 1.55; margin: 0 0 9px; color: #172b42; }
.guide-grid p { font-size: 13px; line-height: 1.75; margin: 8px 0 0; }
.guide-formula { background: #f2f6fa; border-radius: 6px; padding: 8px 10px; }
.guide-formula strong { display: block; font-size: 12px; color: #52647a; margin-bottom: 3px; }
.guide-caveat, .guide-empty { color: #52647a; }
@media (max-width: 540px) {
  .guide-toggle { padding: 14px; }
  .guide-subtitle { display: block; margin: 5px 0 0; }
  .guide-content { padding: 0 12px 12px; }
}
</style>
