/**
 * Whether this device is used by touch first (a phone, a tablet): it has no hover and a coarse
 * pointer, the same test the pages use to show what desktops reveal on hover. Its on-screen
 * keyboard has no Shift+Enter to speak of, so there Enter starts a new line and a message goes
 * only with its Send button.
 */
export function touchFirst(): boolean {
  return typeof window !== 'undefined' && !!window.matchMedia?.('(hover: none) and (pointer: coarse)').matches;
}
