<script setup lang="ts">
import { computed } from 'vue'
import { ChevronDown, ChevronRight } from 'lucide-vue-next'

import type { CaseGroupNode } from '../utils/caseListPresentation'
import type { CaseListItem } from '../utils/caseListPresentation'
import SearchHighlight from './SearchHighlight.vue'

defineOptions({ name: 'CaseGroupBranch' })

const props = withDefaults(defineProps<{
  node: CaseGroupNode
  expandedIds: string[]
  query?: string
  forceExpanded?: boolean
  depth?: number
}>(), {
  query: '',
  forceExpanded: false,
  depth: 0,
})

const emit = defineEmits<{ toggle: [nodeId: string] }>()
defineSlots<{ item(props: { item: CaseListItem }): unknown }>()
const expanded = computed(() => props.forceExpanded || props.expandedIds.includes(props.node.id))
</script>

<template>
  <section class="case-list-group" :class="{ collapsed: !expanded }" :style="{ '--case-group-depth': depth }">
    <h3 :data-testid="`case-list-group-${node.fullPath}`">
      <button
        :data-testid="`case-list-group-toggle-${node.fullPath}`"
        class="case-list-group-toggle"
        type="button"
        :aria-expanded="expanded ? 'true' : 'false'"
        :title="node.fullPath"
        @click="emit('toggle', node.id)"
      >
        <ChevronDown v-if="expanded" :size="15" />
        <ChevronRight v-else :size="15" />
        <span><SearchHighlight :text="node.label" :query="query" /></span>
        <b>{{ node.count }}</b>
      </button>
    </h3>
    <template v-if="expanded">
      <template v-for="item in node.items" :key="`${item.kind}-${item.id}`">
        <slot name="item" :item="item" />
      </template>
      <CaseGroupBranch
        v-for="child in node.children"
        :key="child.id"
        :node="child"
        :expanded-ids="expandedIds"
        :query="query"
        :force-expanded="forceExpanded"
        :depth="depth + 1"
        @toggle="emit('toggle', $event)"
      >
        <template #item="slotProps"><slot name="item" v-bind="slotProps" /></template>
      </CaseGroupBranch>
    </template>
  </section>
</template>
