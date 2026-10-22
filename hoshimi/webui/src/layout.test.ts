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
  ['components/chat/UserMessage.vue', '.bubble'],
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

/**
 * The same margin falls on a menu's items: an item with ``width: 100%`` is wider than the menu by
 * the lane, and a horizontal scrollbar appears under it. Menus stretch their items as flex items.
 */
const MENUS = ['ui/AppMenu.vue', 'ui/ContextMenu.vue', 'ui/ComposerMenu.vue'];
describe('menus', () => {
  it.each(MENUS)('%s stretches its items without a width of their own', (file) => {
    expect(rule(file, '.menu')).toMatch(/flex-direction:\s*column/);
    expect(rule(file, '.item')).not.toMatch(/(^|[\s;])width:\s*100%/);
  });
});

/**
 * A scrollbar's lane sits at an area's end side only (tokens.css), so the areas that keep it pad
 * their end by ``--app-scroll-lane`` less than their start: otherwise content sits a lane further from
 * the end edge than from the start. Menus and pickers (``app-scroll-auto``) give the lane up instead.
 */
const FILL_PAGES = new Set(['views/PlaygroundView.vue']);
const LANE_PADDED: [file: string, selector: string][] = [
  ...PAGES.filter((f) => !FILL_PAGES.has(f.replace(/\\/g, '/'))).map((file): [string, string] => [file, `.${/<template>\s*<\w+[^>]*\bclass="([\w-]+)/.exec(readFileSync(join(SRC, file), 'utf8'))![1]}`]),
  ['ui/AppDialog.vue', '.body'],
  ['ui/SideSheet.vue', '.body'],
  ['ui/DataTable.vue', '.row'],
];
describe('scrollbar lanes', () => {
  it.each(LANE_PADDED)('%s %s takes the lane out of its end padding', (file, selector) => {
    const own = rule(file, selector);
    expect(own).toMatch(/padding-inline-end:/);
    expect(own).toContain('var(--app-scroll-lane)');
  });
  it('a page that fills the frame keeps no shell lane, and its thread reaches into its padding', () => {
    expect(readFileSync(join(SRC, 'router.ts'), 'utf8')).toMatch(/name: 'playground'[^\n]*meta: \{ fill: true \}/);
    expect(readFileSync(join(SRC, 'ui/AppShell.vue'), 'utf8')).toContain(`:class="{ 'app-scroll-auto': fill }"`);
    expect(rule('views/PlaygroundView.vue', '.thread')).toMatch(/margin-inline-end:[^;]*var\(--app-scroll-lane\)/);
  });
  it('the detail pane reaches into the page padding by its lane', () => {
    expect(rule('ui/ListDetail.vue', '.detail-pane')).toMatch(/margin-inline-end:[^;]*var\(--app-scroll-lane\)/);
  });
  it('areas that give the lane up never step back where scrollbars float', () => {
    const css = readFileSync(join(SRC, 'theme/tokens.css'), 'utf8');
    expect(css).toMatch(/:root\[data-scrollbars='overlay'\] \.app-scroll:not\(\.app-scroll-auto\) >/);
  });
});

/**
 * The bottom bar lays its destinations side by side. A destination that keeps its indicator's 64px
 * as a minimum makes six of them 384px, wider than a small phone: the shell's one column then grows
 * past the screen and every page is cut at its end edge.
 */
describe('bottom navigation', () => {
  it('lets its destinations share any width', () => {
    expect(rule('ui/NavigationBar.vue', '.dest')).toMatch(/min-width:\s*0/);
    expect(rule('ui/NavigationBar.vue', '.indicator')).toMatch(/max-width:\s*64px/);
    expect(rule('ui/NavigationBar.vue', '.indicator')).not.toMatch(/(^|[\s;])width:\s*64px/);
  });
});
