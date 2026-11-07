/**
 * Measure actual layout space, not browser identity: scrollbar-gutter does not reserve space for
 * overlay scrollbars. In that case .app-scroll supplies a padding lane instead. Components use
 * the total lane for alignment and add the overlay part to their own scrolling-edge padding.
 */
export function applyScrollbars(root: HTMLElement = document.documentElement): 'overlay' | 'classic' {
  const probe = document.createElement('div');
  probe.className = 'app-scroll';
  Object.assign(probe.style, { position: 'absolute', top: '-200px', width: '100px', height: '100px', overflow: 'scroll', visibility: 'hidden' });
  document.body.appendChild(probe);
  const lane = probe.offsetWidth - probe.clientWidth;
  const overlay = lane > 0 ? 0 : parseFloat(getComputedStyle(probe).getPropertyValue('--app-scrollbar-size')) || 12;
  const kind = lane > 0 ? 'classic' : 'overlay';
  probe.remove();
  root.dataset.scrollbars = kind;
  root.style.setProperty('--app-scroll-lane', `${lane || overlay}px`);
  root.style.setProperty('--app-scroll-overlay', `${overlay}px`);
  return kind;
}
