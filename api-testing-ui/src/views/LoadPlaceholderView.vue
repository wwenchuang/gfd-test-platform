<script setup lang="ts">
import { computed } from 'vue'
import { useRoute } from 'vue-router'

const route = useRoute()
const title = computed(() => String(route.meta.title || '性能测试'))
</script>

<template>
  <section class="workspace" data-testid="load-placeholder">
    <header class="page-toolbar"><div><p class="eyebrow">性能测试</p><h1>{{ title }}</h1></div></header>
    <div class="section-empty">页面正在接入，本次版本先完成压测节点与校准管理。</div>
  </section>
</template>
