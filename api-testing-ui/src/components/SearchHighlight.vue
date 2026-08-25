<script setup lang="ts">
import { computed } from 'vue'

const props = defineProps<{ text: string; query?: string }>()

const parts = computed(() => {
  const query = props.query?.trim() || ''
  if (!query) return [{ text: props.text, match: false }]
  const source = props.text.toLocaleLowerCase()
  const target = query.toLocaleLowerCase()
  const result: Array<{ text: string; match: boolean }> = []
  let offset = 0
  while (offset < props.text.length) {
    const index = source.indexOf(target, offset)
    if (index < 0) {
      result.push({ text: props.text.slice(offset), match: false })
      break
    }
    if (index > offset) result.push({ text: props.text.slice(offset, index), match: false })
    result.push({ text: props.text.slice(index, index + query.length), match: true })
    offset = index + query.length
  }
  return result.length ? result : [{ text: props.text, match: false }]
})
</script>

<template>
  <template v-for="(part, index) in parts" :key="`${index}-${part.text}`">
    <mark v-if="part.match">{{ part.text }}</mark><template v-else>{{ part.text }}</template>
  </template>
</template>
