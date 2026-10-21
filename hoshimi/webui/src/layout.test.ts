import { readdirSync, readFileSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';

/**
 * Rows that pair a name with a control must let the name shrink.
 *
 * A model id is often one long unbreakable word, so the row's min-content width is the whole id. A
 * flex item keeps `min-width: auto` by default and refuses to go below that, which pushes the row's
 * buttons out of view. `min-width: 0` on the item that holds the text is what makes the ellipsis
 * work and keeps the control in place.
 */
const SRC = join(process.cwd(), 'src');
const ROWS: [file: string, selector: string][] = [
  ['ui/Snackbar.vue', '.text'],
  ['ui/Chip.vue', '.label'],
  ['ui/ContextMenu.vue', '.label'],
  ['ui/DataTable.vue', '.cell'],
  ['ui/PageSection.vue', '.titles'],
  ['ui/StatTile.vue', '.label, .detail'],
  ['views/ChannelsView.vue', '.row-text'],
  ['views/OverviewView.vue', '.path'],
  ['views/ModelsView.vue', '.name-cell'],
  ['views/PlaygroundView.vue', '.bubble'],
  ['views/UsageView.vue', '.name-cell'],
  ['components/ChannelDetail.vue', '.account-text'],
  ['components/LiveCallCard.vue', '.titles'],
  ['components/LiveSheet.vue', '.text'],
];

function rule(file: string, selector: string): string {
  const css = readFileSync(join(SRC, file), 'utf8');
  const match = new RegExp(String.raw`(?:^|\n)\s*${selector.replace(/\./g, '\\.')}\s*\{([^}]*)\}`).exec(css);
  expect(match, `${file} has no rule for ${selector}`).not.toBeNull();
  return match![1];
}

describe('rows that hold a file name', () => {
  it.each(ROWS)('%s %s can shrink below the name', (file, selector) => {
    expect(rule(file, selector)).toContain('min-width: 0');
  });
});

/**
 * A list that can grow without bound (models, calls, accounts) scrolls inside itself or renders
 * only what is in view, so a long one never stretches its page into an endless column.
 */
const BOUNDED: [file: string, component: string][] = [
  ['views/ModelsView.vue', '<DataTable'],
  ['views/UsageView.vue', '<DataTable'],
  ['components/ChannelDetail.vue', '<ScrollArea'],
];
describe('lists that can grow without bound', () => {
  it.each(BOUNDED)('%s keeps its list in a %s', (file, component) => {
    expect(readFileSync(join(SRC, file), 'utf8')).toContain(component);
  });
});

/**
 * Every scrolling area wears ``.app-scroll`` (tokens.css), which gives its scrollbar a lane of its
 * own beside the content instead of floating over it. A component that scrolls by its own
 * ``overflow: auto`` would bring the overlay back; one that sets ``scrollbar-width`` or
 * ``scrollbar-color`` would switch the lane off in Chromium (which then ignores the styled
 * scrollbar). Only the rail hides its scrollbar on purpose.
 */
const VUE_FILES = (readdirSync(SRC, { recursive: true }) as string[]).filter((f) => f.endsWith('.vue'));
const HIDDEN_SCROLLBAR = new Set(['ui/NavigationRail.vue']);
describe('scrolling areas', () => {
  it.each(VUE_FILES)('%s scrolls only through .app-scroll', (file) => {
    const source = readFileSync(join(SRC, file), 'utf8');
    const css = source.slice(source.indexOf('<style'));
    expect(css, 'an overflow that scrolls belongs to .app-scroll').not.toMatch(/overflow(-[xy])?:\s*(auto|scroll)\b(?![^;]*hidden)/);
    if (!HIDDEN_SCROLLBAR.has(file.replace(/\\/g, '/'))) expect(css).not.toMatch(/scrollbar-(width|color|gutter)\s*:/);
  });
});

/**
 * A page is the scroller's only child and fills its width as a block. Where scrollbars float
 * (Firefox with overlay scrollbars), tokens.css gives that child a ``margin-inline-end`` the
 * lane's width; a page that also sets ``width: 100%`` is then wider than the scroller by that
 * margin, and a horizontal scrollbar appears though everything fits.
 */
const PAGES = VUE_FILES.filter((f) => f.replace(/\\/g, '/').startsWith('views/'));
describe('pages', () => {
  it.each(PAGES)('%s fills the scroller without a width of its own', (file) => {
    const source = readFileSync(join(SRC, file), 'utf8');
    const root = /<template>\s*<\w+[^>]*\bclass="([\w-]+)/.exec(source)?.[1];
    expect(root, `${file} has no root class`).toBeTruthy();
    expect(rule(file, `.${root}`)).not.toMatch(/(^|[\s;])width:\s*100%/);
  });
});

/**
 * A component a pane switch replaces (AxisPanes, FadeSwap, <Transition>) is moved by the motion
 * system's named transition on its root element. A ``transition`` the component declares on that
 * root (scoped, so more specific) replaces the named one: the pane's slide then jumps to its end in
 * one frame while only its own property eases. Such a component keeps its own transitions inside.
 * The exceptions are never a pane themselves.
 */
const OWN_ROOT_TRANSITION = new Set(['ui/AppCard.vue', 'ui/StackedBarChart.vue']);
const UI_FILES = VUE_FILES.filter((f) => f.replace(/\\/g, '/').startsWith('ui/') && !OWN_ROOT_TRANSITION.has(f.replace(/\\/g, '/')));
describe('components a pane switch moves', () => {
  it.each(UI_FILES)('%s leaves its root element to the motion system', (file) => {
    const source = readFileSync(join(SRC, file), 'utf8');
    const root = /<template>\s*<[\w-]+[^>]*?\bclass="([\w-]+)/.exec(source)?.[1];
    if (!root) return;
    const css = source.slice(source.indexOf('<style'));
    const own = new RegExp(String.raw`(?:^|\n)\s*\.${root}\s*\{([^}]*)\}`).exec(css)?.[1] ?? '';
    expect(own).not.toMatch(/(^|[\s;])transition(-property)?\s*:/);
  });
});
