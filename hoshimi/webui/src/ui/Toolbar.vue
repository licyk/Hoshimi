<script setup lang="ts">
/**
 * The row above a page's sections: what scopes the page (tabs, a range, filters, a search) at the
 * start, the page's own actions at the end. One row that wraps rather than a stack of rows, so the
 * content starts at the same height on every page.
 */
</script>

<template>
  <div class="toolbar">
    <div class="start"><slot /></div>
    <div v-if="$slots.end" class="end"><slot name="end" /></div>
  </div>
</template>

<style scoped>
.toolbar { display: flex; align-items: center; flex-wrap: wrap; gap: var(--app-space-2) var(--app-space-3); min-width: 0; }
.start { display: flex; align-items: center; flex-wrap: wrap; gap: var(--app-space-2); flex: 1 1 280px; min-width: 0; }
.end { display: flex; align-items: center; flex-wrap: wrap; gap: var(--app-space-2); margin-left: auto; }
</style>
