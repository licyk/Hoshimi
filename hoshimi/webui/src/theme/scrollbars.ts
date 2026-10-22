/**
 * Whether this browser draws scrollbars over the content (Firefox on macOS or GTK with overlay
 * scrollbars) even on an ``.app-scroll`` area. Chromium and WebKit never do there: a styled
 * scrollbar takes its own lane. Where they float, ``data-scrollbars="overlay"`` on the root makes
 * the areas' content step back by the lane's width instead (tokens.css). Where they take a lane,
 * ``--app-scroll-lane`` is set to its measured width, which areas take out of their end padding.
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
  if (kind === 'classic') root.style.setProperty('--app-scroll-lane', `${lane}px`);
  else root.style.removeProperty('--app-scroll-lane');
  return kind;
}
