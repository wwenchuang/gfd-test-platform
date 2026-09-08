<script setup lang="ts">
export type TestContext = { purpose: string; release: string; data_profile: string; cache_state: string; notes: string }
export type StopPolicy = { http_error_rate: number; grace_seconds: number } | null
const props = defineProps<{ modelValue: TestContext; stopPolicy: StopPolicy }>()
const emit = defineEmits<{ 'update:modelValue': [TestContext]; 'update:stopPolicy': [StopPolicy] }>()
function field(key: keyof TestContext, event: Event) { emit('update:modelValue', { ...props.modelValue, [key]: (event.target as HTMLInputElement).value }) }
function stopField(key: 'http_error_rate' | 'grace_seconds', event: Event) { emit('update:stopPolicy', { ...(props.stopPolicy || {http_error_rate: .2, grace_seconds: 30}), [key]: Number((event.target as HTMLInputElement).value) / (key === 'http_error_rate' ? 100 : 1) }) }
</script>
<template>
<section class="test-intent" aria-label="测试目的和条件">
<h3>测试目的与可比条件</h3>
<div class="intent-fields">
<label>本次目的<select :value="modelValue.purpose" @change="field('purpose', $event)"><option value="smoke">流程冒烟 · 确认脚本能运行</option><option value="load">日常负载 · 验证预期业务量</option><option value="stress">逐步加压 · 寻找性能拐点</option><option value="spike">突发流量 · 观察冲击和恢复</option><option value="soak">长时稳定性 · 观察资源趋势</option></select></label>
<label>被测业务版本<input :value="modelValue.release" maxlength="1000" placeholder="例如 1.20.0 / 发布单编号" @input="field('release', $event)" /></label>
<label>数据与账号规模<input :value="modelValue.data_profile" maxlength="1000" placeholder="例如 100 个测试账号、1 万条模型数据；勿填密码" @input="field('data_profile', $event)" /></label>
<label>缓存状态<select :value="modelValue.cache_state" @change="field('cache_state', $event)"><option value="">未记录（对比时提示不可确定）</option><option value="warm">已预热</option><option value="cold">冷缓存</option><option value="mixed">混合状态</option></select></label>
<label class="intent-wide">测试边界与清理安排<input :value="modelValue.notes" maxlength="1000" placeholder="例如仅测试环境；导入完成后清理本次模型；不触发真实设备" @input="field('notes', $event)" /></label>
</div>
<p>目的不会自动决定安全的压力值。长时测试还受节点最大执行时长限制；异步任务需验证最终完成，真实打印和验证码不可直接批量放大。</p>
<label class="intent-check"><input type="checkbox" :checked="Boolean(stopPolicy)" @change="emit('update:stopPolicy', ($event.target as HTMLInputElement).checked ? { http_error_rate: .2, grace_seconds: 30 } : null)" /><span>启用 HTTP 错误保护停止</span></label>
<div v-if="stopPolicy" class="intent-fields"><label>累计 HTTP 错误率上限（%）<input type="number" min="0.1" max="100" step="0.1" :value="stopPolicy.http_error_rate * 100" @input="stopField('http_error_rate', $event)" /></label><label>首次判定宽限时间（秒）<input type="number" min="10" max="600" :value="stopPolicy.grace_seconds" @input="stopField('grace_seconds', $event)" /></label></div>
<p v-if="stopPolicy">由各压力节点按本节点累计 HTTP 请求判定，不是最近窗口错误率；宽限期后超限会中止该节点，平台同时通知其他节点停止，并按节点失败处理本轮。尚在服务端运行的异步任务不会被自动撤销，请安排清理与恢复检查。</p>
</section>
</template>
<style scoped>
.test-intent{display:grid;gap:12px;padding:16px;border:1px solid #dce5ee;border-radius:8px;background:#f8fafc}.test-intent h3,.test-intent p{margin:0}.test-intent p{font-size:13px;line-height:1.6;color:#617086}.intent-fields{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}.intent-fields label{display:grid;gap:6px;min-width:0;font-size:13px}.intent-fields input,.intent-fields select{min-height:36px;padding:7px;box-sizing:border-box;min-width:0}.intent-wide{grid-column:1/-1}.intent-check{display:flex;align-items:center;gap:8px;font-size:13px}.intent-check input{width:18px;height:18px;flex:0 0 18px;margin:0}@media(max-width:720px){.intent-fields{grid-template-columns:minmax(0,1fr)}}
</style>
