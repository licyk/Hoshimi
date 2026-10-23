import { flushPromises, mount } from '@vue/test-utils';
import { afterEach, describe, expect, it } from 'vitest';
import { defineComponent, h, ref } from 'vue';
import ImageViewer from '@/ui/ImageViewer.vue';
import { useImageViewer } from '@/ui/useImageViewer';

/** A part of a page with three images marked for the viewer, one of them broken, and the viewer. */
const Page = defineComponent({
  setup() {
    const root = ref<HTMLElement | null>(null);
    const viewer = useImageViewer(() => root.value);
    return () =>
      h('div', [
        h('div', { ref: root, onClick: viewer.onClick }, [
          h('button', { class: 'one', 'data-view': 'blob:one', 'data-alt': 'first.png' }),
          h('button', { class: 'broken', 'data-view': 'https://x/broken.png', 'data-alt': 'gone' }),
          h('p', [h('button', { class: 'two', 'data-view': 'data:image/png;base64,AA==', 'data-alt': 'second' }, [h('img')])]),
        ]),
        h(ImageViewer, {
          open: viewer.open.value,
          'onUpdate:open': (v: boolean) => (viewer.open.value = v),
          index: viewer.index.value,
          'onUpdate:index': (v: number) => (viewer.index.value = v),
          images: viewer.images.value,
          fromRect: viewer.fromRect.value,
        }),
      ]);
  },
});

const dialog = () => document.body.querySelector<HTMLElement>('[role="dialog"]');
const shown = () => dialog()?.querySelector<HTMLImageElement>('img.picture');
/** IconButton is a Material icon button: the element carrying the label is the one clicked. */
const control = (label: string) => dialog()!.querySelector<HTMLElement>(`[aria-label="${label}"]`);
const press = (label: string) => control(label)!.click();

describe('ImageViewer', () => {
  afterEach(() => {
    document.body.innerHTML = '';
  });

  it('opens on the image pressed, with every working image of the page to move between', async () => {
    const w = mount(Page, { attachTo: document.body });
    await w.find('.two').trigger('click');
    await flushPromises();
    expect(dialog()).not.toBeNull();
    expect(shown()!.getAttribute('src')).toBe('data:image/png;base64,AA==');
    expect(dialog()!.textContent).toContain('2 / 2');
    expect(dialog()!.textContent).toContain('second');
    press('Next image');
    await flushPromises();
    expect(shown()!.getAttribute('src')).toBe('blob:one');
    dialog()!.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowLeft', bubbles: true }));
    await flushPromises();
    expect(shown()!.getAttribute('src')).toBe('data:image/png;base64,AA==');
    w.unmount();
  });

  it('zooms in and back, offers the original only where a tab can open it, and closes', async () => {
    const w = mount(Page, { attachTo: document.body });
    await w.find('.two').trigger('click');
    await flushPromises();
    expect(control('Open in a new tab')).toBeNull();
    press('Zoom in');
    await flushPromises();
    expect(shown()!.style.transform).toBe('translate(0px, 0px) scale(1.5)');
    dialog()!.dispatchEvent(new KeyboardEvent('keydown', { key: '0', bubbles: true }));
    await flushPromises();
    expect(shown()!.style.transform).toBe('translate(0px, 0px) scale(1)');
    press('Next image');
    await flushPromises();
    expect(control('Open in a new tab')).not.toBeNull();
    press('Close');
    await flushPromises();
    expect(dialog()).toBeNull();
    w.unmount();
  });
});
