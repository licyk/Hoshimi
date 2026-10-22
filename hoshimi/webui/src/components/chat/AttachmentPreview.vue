<script setup lang="ts">
import { computed, ref, watch } from 'vue';
import { attachmentUrl } from '@/api/conversations';
import { size } from '@/components/chat/attachments';
import { useI18n } from '@/i18n';
import { AppIcon, ProgressCircle, icons } from '@/ui';

/**
 * One attachment, as a thumbnail (an image) or a card (a file: its name, and its type and size). An
 * image opens at full size in a new tab. In the composer it can be taken back (``remove``) and shows
 * that it is still uploading.
 */
const props = defineProps<{
  kind: 'image' | 'file';
  name: string;
  mime?: string;
  bytes?: number;
  /** The kept attachment, fetched for its image; or ``url``, a local preview. */
  id?: string;
  url?: string;
  removable?: boolean;
  uploading?: boolean;
}>();
const emit = defineEmits<{ remove: [] }>();
const { t } = useI18n();

const src = ref(props.url ?? '');
watch(
  () => [props.id, props.url, props.kind] as const,
  async ([id, url, kind]) => {
    if (url) src.value = url;
    else if (id && kind === 'image') src.value = await attachmentUrl(id).catch(() => '');
  },
  { immediate: true },
);
const type = computed(() => {
  const ext = props.name.includes('.') ? props.name.slice(props.name.lastIndexOf('.') + 1).toUpperCase() : '';
  return props.mime === 'application/pdf' ? 'PDF' : ext || t('playground.attach');
});
const open = () => src.value && window.open(src.value, '_blank', 'noopener');
</script>

<template>
  <div class="attachment" :class="kind">
    <button v-if="kind === 'image'" type="button" class="thumb" :aria-label="`${t('playground.openImage')}: ${name}`" :title="name" @click="open">
      <img v-if="src" :src="src" :alt="name" />
    </button>
    <div v-else class="card" :title="name">
      <span class="icon"><AppIcon :icon="icons.FileText" :size="20" /></span>
      <span class="text">
        <span class="name type-body-medium">{{ name }}</span>
        <span class="meta type-body-small muted">{{ type }}<template v-if="bytes"> · {{ size(bytes) }}</template></span>
      </span>
    </div>
    <span v-if="uploading" class="busy" :aria-label="t('playground.uploading')"><ProgressCircle :size="20" /></span>
    <button v-if="removable" type="button" class="remove" :aria-label="t('playground.removeAttachment', { name })" :title="t('playground.removeAttachment', { name })" @click="emit('remove')">
      <AppIcon :icon="icons.X" :size="18" />
    </button>
  </div>
</template>

<style scoped>
.attachment { position: relative; flex: none; }
.thumb {
  display: block; width: 64px; height: 64px; padding: 0; overflow: hidden; border: 1px solid var(--md-sys-color-outline-variant);
  border-radius: var(--md-sys-shape-corner-medium); background: var(--md-sys-color-surface-container-high); cursor: zoom-in;
}
.thumb img { display: block; width: 100%; height: 100%; object-fit: cover; }
.card {
  display: flex; align-items: center; gap: var(--app-space-2); max-width: 240px; height: 56px; padding: var(--app-space-2) var(--app-space-3) var(--app-space-2) var(--app-space-2);
  border: 1px solid var(--md-sys-color-outline-variant); border-radius: var(--md-sys-shape-corner-medium); background: var(--md-sys-color-surface-container-low);
}
.icon { display: grid; place-items: center; flex: none; width: 36px; height: 36px; border-radius: var(--md-sys-shape-corner-small); background: var(--md-sys-color-tertiary-container); color: var(--md-sys-color-on-tertiary-container); }
.text { display: flex; flex-direction: column; min-width: 0; }
.name, .meta { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.busy { position: absolute; inset: 0; display: grid; place-items: center; border-radius: var(--md-sys-shape-corner-medium); background: color-mix(in srgb, var(--md-sys-color-surface) 60%, transparent); }
.remove {
  position: absolute; top: -6px; right: -6px; display: grid; place-items: center; width: 20px; height: 20px; padding: 0; border: 0;
  border-radius: 50%; background: var(--md-sys-color-inverse-surface); color: var(--md-sys-color-inverse-on-surface); cursor: pointer;
}
</style>
