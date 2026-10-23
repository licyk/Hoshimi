import { ref } from 'vue';
import type { ViewerImage } from '@/ui/ImageViewer.vue';

/**
 * The images of a part of the page, for ``ImageViewer``: any element in it marked ``data-view`` (the
 * address to show) and ``data-alt`` (what it shows) opens the viewer when pressed, with every such
 * element there, in the order they stand, as the images to move between. What marks an image needs
 * no part in this: an attachment's thumbnail, an image in a reply and a drawn diagram all just say so.
 */
export function useImageViewer(root: () => HTMLElement | null | undefined) {
  const open = ref(false);
  const images = ref<ViewerImage[]>([]);
  const index = ref(0);
  const fromRect = ref<DOMRect | null>(null);

  function onClick(event: MouseEvent) {
    const container = root();
    const target = (event.target as Element | null)?.closest<HTMLElement>('[data-view]');
    if (!container || !target || !container.contains(target) || target.classList.contains('broken')) return;
    event.preventDefault();
    const marked = [...container.querySelectorAll<HTMLElement>('[data-view]')].filter((el) => !el.classList.contains('broken'));
    images.value = marked.map((el) => ({ src: el.dataset.view ?? '', alt: el.dataset.alt ?? '' }));
    index.value = Math.max(0, marked.indexOf(target));
    fromRect.value = (target.querySelector('img, svg') ?? target).getBoundingClientRect();
    open.value = true;
  }

  return { open, images, index, fromRect, onClick };
}
