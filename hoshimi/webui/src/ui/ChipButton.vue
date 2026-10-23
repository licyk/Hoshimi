<script setup lang="ts">
import type { Component } from 'vue';
import AppIcon from '@/ui/AppIcon.vue';
import ProgressCircle from '@/ui/ProgressCircle.vue';
import { ChevronDown } from '@/ui/icons';

/**
 * A quiet, pill-shaped control for a row of tools inside a larger surface (the composer's model and
 * effort, its Clear): no outline until hovered, an icon and a short label, and a chevron when it
 * opens a menu. With no label it is a round icon button; ``label`` is then its accessible name.
 */
withDefaults(
  defineProps<{
    icon?: Component;
    /** Shown beside the icon; long ones end in an ellipsis. */
    text?: string;
    /** The accessible name and tooltip when ``text`` is not enough or absent. */
    label?: string;
    /** Ends in a chevron that turns over while ``open``. */
    menu?: boolean;
    open?: boolean;
    /** Shown in the error colours: a choice with consequences (tools that never ask). */
    danger?: boolean;
    disabled?: boolean;
    /** Spins in place of the icon and does not respond: what it shows is still coming. */
    loading?: boolean;
  }>(),
  { text: '' },
);
defineEmits<{ click: [MouseEvent] }>();
</script>

<template>
  <span class="chip-button">
    <button
      type="button"
      class="face state-layer type-label-large"
      :class="{ round: !text, open, danger }"
      :disabled="disabled"
      :aria-busy="loading || undefined"
      :aria-label="label || undefined"
      :aria-haspopup="menu ? 'listbox' : undefined"
      :aria-expanded="menu ? open : undefined"
      :title="label || undefined"
      @click="!loading && $emit('click', $event)"
    >
      <ProgressCircle v-if="loading" :size="18" class="lead" />
      <AppIcon v-else-if="icon" :icon="icon" :size="18" class="lead" />
      <span v-if="text" class="text">{{ text }}</span>
      <AppIcon v-if="menu" :icon="ChevronDown" :size="18" class="chevron" />
    </button>
  </span>
</template>

<style scoped>
/* The face eases its own states; the root is left to a transition that brings it in or out. */
.chip-button { display: inline-flex; min-width: 0; max-width: 100%; }
.face {
  display: inline-flex; align-items: center; gap: 6px; height: 32px; max-width: 100%; min-width: 0; padding: 0 10px;
  border: 1px solid transparent; border-radius: var(--md-sys-shape-corner-full); background: transparent;
  color: var(--md-sys-color-on-surface-variant); font: inherit; cursor: pointer; white-space: nowrap;
  transition:
    color var(--md-sys-motion-duration-short4) var(--md-sys-motion-easing-standard),
    background-color var(--md-sys-motion-duration-short4) var(--md-sys-motion-easing-standard),
    border-color var(--md-sys-motion-duration-short4) var(--md-sys-motion-easing-standard),
    transform var(--md-sys-motion-duration-short3) var(--md-sys-motion-easing-standard);
}
.face:hover:not(:disabled), .face.open { color: var(--md-sys-color-on-surface); border-color: var(--md-sys-color-outline-variant); }
.face:active:not(:disabled) { transform: scale(0.96); }
.face:disabled { opacity: var(--app-disabled-content-opacity); cursor: default; }
.face[aria-busy='true'] { cursor: progress; }
.face.danger { color: var(--md-sys-color-on-error-container); background: var(--md-sys-color-error-container); }
.face.danger:hover:not(:disabled), .face.danger.open { color: var(--md-sys-color-on-error-container); border-color: var(--md-sys-color-error); }
.round { justify-content: center; width: 32px; padding: 0; }
.lead { --md-circular-progress-active-indicator-color: currentColor; }
/* A model id is one long word: cut it, the menu shows it whole. */
.text { min-width: 0; overflow: hidden; text-overflow: ellipsis; }
.chevron { margin-inline-start: -2px; opacity: 0.8; transition: transform var(--md-sys-motion-duration-medium1) var(--md-sys-motion-easing-emphasized); }
.open .chevron { transform: rotate(180deg); }
</style>
