/**
 * Whether this browser draws scrollbars over the content (Firefox on macOS or GTK with overlay
 * scrollbars) even on an ``.app-scroll`` area. Chromium and WebKit never do there: a styled
 * scrollbar takes its own lane. ``--app-scroll-lane`` is the room the lane takes, which areas take
 * out of their end padding: its measured width, or 0 where scrollbars float and take none (the
 * thumb then passes over the end padding while it scrolls).
 */
export function applyScrollbars(root: HTMLElement = document.documentElement): 'overlay' | 'classic' {
  const probe = document.createElement('div');
  probe.className = 'app-scroll';
  Object.assign(probe.style, { position: 'absolute', top: '-200px', width: '100px', height: '100px', overflow: 'scroll', visibility: 'hidden' });
  document.body.appendChild(probe);
  const lane = probe.offsetWidth - probe.clientWidth;
  const kind = lane > 0 ? 'classic' : 'overlay';
  probe.remove();
  root.dataset.scrollbars = kind;
  root.style.setProperty('--app-scroll-lane', `${lane}px`);
  return kind;
}
