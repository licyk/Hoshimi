/**
 * Whether this browser draws scrollbars over the content (Firefox on macOS or GTK with overlay
 * scrollbars) even on an ``.app-scroll`` area. Chromium and WebKit never do there: a styled
 * scrollbar takes its own lane. Where they float, ``data-scrollbars="overlay"`` on the root makes
 * the areas' content step back by the lane's width instead (tokens.css).
 */
export function applyScrollbars(root: HTMLElement = document.documentElement): 'overlay' | 'classic' {
  const probe = document.createElement('div');
  probe.className = 'app-scroll';
  Object.assign(probe.style, { position: 'absolute', top: '-200px', width: '100px', height: '100px', overflow: 'scroll', visibility: 'hidden' });
  document.body.appendChild(probe);
  const kind = probe.offsetWidth - probe.clientWidth > 0 ? 'classic' : 'overlay';
  probe.remove();
  root.dataset.scrollbars = kind;
  return kind;
}
