<script setup lang="ts">
import { Database, Save } from 'lucide-vue-next'

defineProps<{ projectId: string | null; sourceRevisionId: string | null; environmentRevisionId: string | null; loading?: boolean; saved?: boolean }>()
defineEmits<{ save: [] }>()
</script>

<template>
  <div class="context-bar" aria-label="当前测试上下文">
    <div class="context-heading"><Database :size="17" /><strong>当前范围</strong></div>
    <dl>
      <div><dt>项目</dt><dd>{{ projectId || '未选择' }}</dd></div>
      <div><dt>接口版本</dt><dd>{{ sourceRevisionId || '未选择' }}</dd></div>
      <div><dt>执行环境</dt><dd>{{ environmentRevisionId || '未选择' }}</dd></div>
    </dl>
    <span v-if="saved" class="saved-state" role="status">已保存</span>
    <button class="icon-command" type="button" title="保存当前范围" :disabled="loading" @click="$emit('save')"><Save :size="17" /></button>
  </div>
</template>
