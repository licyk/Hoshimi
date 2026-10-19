import { argbFromHex, hexFromArgb, Hct, SchemeTonalSpot } from '@material/material-color-utilities';

/** Starlight indigo seeds the light and dark palettes. Users can choose their own accent. */
export const DEFAULT_SOURCE_COLOR = '#5267d8';

export const ROLES = [
  'primary', 'onPrimary', 'primaryContainer', 'onPrimaryContainer',
  'secondary', 'onSecondary', 'secondaryContainer', 'onSecondaryContainer',
  'tertiary', 'onTertiary', 'tertiaryContainer', 'onTertiaryContainer',
  'error', 'onError', 'errorContainer', 'onErrorContainer',
  'surface', 'onSurface', 'surfaceVariant', 'onSurfaceVariant',
  'surfaceDim', 'surfaceBright',
  'surfaceContainerLowest', 'surfaceContainerLow', 'surfaceContainer',
  'surfaceContainerHigh', 'surfaceContainerHighest',
  'inverseSurface', 'inverseOnSurface', 'inversePrimary',
  'outline', 'outlineVariant', 'shadow', 'scrim', 'surfaceTint',
] as const;

export type Role = (typeof ROLES)[number];

export const toKebab = (s: string) => s.replace(/[A-Z]/g, (c) => `-${c.toLowerCase()}`);

/** Generate every colour role for one source colour, mode and contrast level (0 standard, 0.5 medium, 1 high). */
export function generateScheme(sourceHex: string, isDark: boolean, contrast = 0): Record<Role, string> {
  let argb: number;
  try {
    argb = argbFromHex(sourceHex);
  } catch {
    argb = argbFromHex(DEFAULT_SOURCE_COLOR);
  }
  const scheme = new SchemeTonalSpot(Hct.fromInt(argb), isDark, contrast);
  const out = {} as Record<Role, string>;
  for (const role of ROLES) {
    out[role] = hexFromArgb((scheme as unknown as Record<Role, number>)[role]);
  }
  return out;
}
