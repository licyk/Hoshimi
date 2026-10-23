<script setup lang="ts">
import { computed } from 'vue';
import type { MessageView } from '@/api/types';
import { AxisPanes } from '@/ui';

/**
 * The thread from ``from`` on, with each turn that has other versions (edits of a question, retries
 * of a reply) standing in a pane of its own together with everything after it: moving to another
 * version slides that turn and what follows it the way the arrow pointed (``AxisPanes``, as tabs
 * do), while what is above stays put. A pane is named by the version's parent and its place among
 * its siblings, not by its id, so a reply taking its kept id as it streams stays where it is. The
 * default slot renders one message, with its index in ``path``.
 */
const props = withDefaults(defineProps<{ path: readonly MessageView[]; siblings: (m: MessageView) => readonly MessageView[]; from?: number; versioned?: boolean }>(), {
  from: 0,
  versioned: false,
});
defineSlots<{ default(props: { message: MessageView; index: number }): unknown }>();

/** Where the next turn with versions starts (a pane's own first message is its version, not the next one). */
const next = computed(() => {
  for (let i = props.from + (props.versioned ? 1 : 0); i < props.path.length; i++) if (props.siblings(props.path[i]).length > 1) return i;
  return -1;
});
const head = computed(() => props.path.slice(props.from, next.value < 0 ? undefined : next.value).map((message, i) => ({ message, index: props.from + i })));
const version = computed(() => {
  const m = props.path[next.value];
  if (!m) return null;
  const list = props.siblings(m);
  const key = (i: number) => `${m.parent_id ?? ''}/${i}`;
  return { key: key(list.findIndex((s) => s.id === m.id)), order: list.map((_, i) => key(i)) };
});
</script>

<template>
  <div class="turns">
    <template v-for="{ message, index } in head" :key="message.id"><slot :message="message" :index="index" /></template>
    <div v-if="version" class="version-panes">
      <AxisPanes :value="version.key" :order="version.order">
        <div :key="version.key" class="pane">
          <VersionPanes v-slot="turn" :path="path" :siblings="siblings" :from="next" versioned><slot v-bind="turn" /></VersionPanes>
        </div>
      </AxisPanes>
    </div>
  </div>
</template>

<style scoped>
.turns { display: flex; flex-direction: column; gap: var(--app-space-4); min-width: 0; }
/* The panes clip sideways as they slide: the box reaches a step past the column on both sides and
   the pane gives it back, so what a message reaches past its edge (its actions' hover pill, a focus
   ring) is not cut off. */
.version-panes { min-width: 0; margin-inline: calc(-1 * var(--app-space-2)); }
.pane { min-width: 0; padding-inline: var(--app-space-2); }
</style>
