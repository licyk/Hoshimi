import { flushPromises, mount } from '@vue/test-utils';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { defineComponent, h } from 'vue';
import Markdown, { COPIED_MS, diagramType, renderMarkdown } from '@/ui/Markdown.vue';
import { naturalSize } from '@/ui/mermaid';
import { MARKDOWN_CONTEXT, allowedMedia, blockModes, type RemoteMedia } from '@/ui/markdownRich';

// Mermaid itself is large and draws with a real layout engine: here it answers with a fixed drawing.
const mermaid = vi.hoisted(() => ({
  initialize: vi.fn(),
  render: vi.fn(async (_id: string, source: string) => {
    if (source.includes('broken')) throw new Error('Parse error on line 1\nmore');
    // As Mermaid sizes a drawing: a share of its box, its own width the most.
    if (source.includes('wide')) return { svg: '<svg width="100%" style="max-width: 1000px;" viewBox="0 0 1000 200"><text>wide</text></svg>' };
    return { svg: `<svg viewBox="0 0 10 10"><text>${source.length}</text></svg>` };
  }),
}));
vi.mock('mermaid', () => ({ default: mermaid }));

const render = (source: string) => mount(Markdown, { props: { source, copyLabel: 'Copy code', copiedLabel: 'Copied' } });
/** As a page that chose how remote media loads renders it. */
const withMedia = (source: string, remoteMedia: RemoteMedia) =>
  mount(
    defineComponent({ setup: () => () => h(Markdown, { source, copyLabel: 'Copy code', copiedLabel: 'Copied' }) }),
    { global: { provide: { [MARKDOWN_CONTEXT as symbol]: { remoteMedia: () => remoteMedia, labels: () => undefined } } } },
  );
const clipboard = (writeText: (text: string) => Promise<void>) => vi.stubGlobal('navigator', { ...navigator, clipboard: { writeText } });

describe('Markdown', () => {
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
    allowedMedia.clear();
    blockModes.clear();
  });

  it('renders the usual blocks and inline marks', () => {
    const w = render('# Title\n\nSome **bold**, *italic* and `code`.\n\n- one\n- two\n\n1. first\n\n> quoted');
    expect(w.find('h1').text()).toBe('Title');
    expect(w.find('strong').text()).toBe('bold');
    expect(w.find('em').text()).toBe('italic');
    expect(w.find('p code').text()).toBe('code');
    expect(w.findAll('ul li').map((li) => li.text())).toEqual(['one', 'two']);
    expect(w.find('ol li').text()).toBe('first');
    expect(w.find('blockquote').text()).toBe('quoted');
  });

  it('breaks a line where the reply does, as a chat does', () => {
    expect(renderMarkdown('one\ntwo')).toBe('<p>one<br>\ntwo</p>\n');
  });

  it('puts code blocks and tables in a scrolling lane of their own', () => {
    const w = render('```\nplain\n```\n\n| a | b |\n|---|---|\n| 1 | 2 |');
    expect(w.find('.code-block > pre').classes()).toEqual(['app-scroll', 'app-scroll-x', 'app-scroll-auto']);
    expect(w.find('.table.app-scroll.app-scroll-x > table td').text()).toBe('1');
  });

  it('highlights a block in a language it knows, by name or alias, and leaves others as text', () => {
    const w = render('```ts\nconst x: number = 1; // one\n```\n\n```py\ndef f(): return "s"\n```\n\n```nosuchlang\nif <x> then\n```');
    const [ts, py, other] = w.findAll('.code-block');
    expect(ts.find('.code-lang').text()).toBe('TypeScript');
    expect(py.find('.code-lang').text()).toBe('Python');
    expect(other.find('.code-lang').text()).toBe('nosuchlang');
    expect(ts.find('code').classes()).toEqual(['hljs', 'language-ts']);
    expect(ts.find('.hljs-keyword').text()).toBe('const');
    expect(ts.find('.hljs-comment').text()).toBe('// one');
    expect(ts.find('code').text()).toBe('const x: number = 1; // one');
    expect(py.find('.hljs-string').text()).toBe('"s"');
    expect(other.find('code').classes()).toEqual(['hljs']);
    expect(other.find('code').element.innerHTML).toBe('if &lt;x&gt; then\n');
  });

  it('heads each block with a toolbar: an icon for what it holds, its language (Text for none), and copy', () => {
    const w = render('```\nplain\n```\n\n```bash\nls\n```\n\n```diff\n-a\n+b\n```\n\n    indented');
    const heads = w.findAll('.code-head');
    expect(heads.map((h) => h.find('.code-lang').text())).toEqual(['Text', 'Bash', 'Diff', 'Text']);
    for (const head of heads) {
      expect(head.find('.code-title > svg.code-icon').exists()).toBe(true);
      expect(head.find('.code-tools > button.code-copy:last-child').exists()).toBe(true);
    }
    // Each kind has its own icon: text, a command and a diff differ.
    const icons = heads.slice(0, 3).map((h) => h.find('.code-icon').element.innerHTML);
    expect(new Set(icons).size).toBe(3);
  });

  it('copies a code block’s code, without its last newline, and says so on the page', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    clipboard(writeText);
    const w = render('Before\n\n```bash\npip install -U hoshimi\nhoshimi serve\n```\n\n    indented code\n');
    const buttons = w.findAll('button.code-copy');
    expect(buttons.map((b) => b.attributes('aria-label'))).toEqual(['Copy code', 'Copy code']);
    await buttons[0].trigger('click');
    await buttons[1].trigger('click');
    await w.find('p').trigger('click');
    await flushPromises();
    expect(writeText.mock.calls).toEqual([['pip install -U hoshimi\nhoshimi serve'], ['indented code']]);
    expect(w.emitted('copied')).toEqual([['pip install -U hoshimi\nhoshimi serve'], ['indented code']]);
  });

  it('shows a check on the button it copied from, for a moment, on the same button', async () => {
    vi.useFakeTimers();
    clipboard(vi.fn().mockResolvedValue(undefined));
    const w = render('```\none\n```\n\n```\ntwo\n```');
    const [first, second] = w.findAll('button.code-copy');
    expect(first.findAll('svg').map((s) => s.classes()[0])).toEqual(['icon-copy', 'icon-check']);
    await first.trigger('click');
    await flushPromises();
    // The same element, so focus stays put: the check is set on it, not rendered anew.
    expect(w.find('button.code-copy').element).toBe(first.element);
    expect(first.classes()).toContain('copied');
    expect(first.attributes('aria-label')).toBe('Copied');
    expect(second.classes()).not.toContain('copied');
    vi.advanceTimersByTime(COPIED_MS);
    expect(first.classes()).not.toContain('copied');
    expect(first.attributes('aria-label')).toBe('Copy code');
  });

  it('shows no check when the clipboard refuses, and says that instead', async () => {
    clipboard(vi.fn().mockRejectedValue(new Error('denied')));
    const w = render('```\none\n```');
    await w.find('button.code-copy').trigger('click');
    await flushPromises();
    expect(w.find('button.code-copy').classes()).not.toContain('copied');
    expect(w.emitted('copyFailed')).toHaveLength(1);
    expect(w.emitted('copied')).toBeUndefined();
  });

  it('shows a reply’s own HTML as text and refuses script links', () => {
    const w = render('<script>alert(1)</script> <img src=x onerror=alert(1)>\n\n[click](javascript:alert(1)) [data](data:text/html,<b>x</b>)');
    expect(w.find('script').exists()).toBe(false);
    expect(w.find('img').exists()).toBe(false);
    expect(w.text()).toContain('<script>alert(1)</script>');
    expect(w.findAll('a')).toHaveLength(0);
  });

  it('opens links beside the app, and links bare addresses', () => {
    const w = render('[docs](https://example.com/docs) and https://example.org');
    const links = w.findAll('a');
    expect(links.map((a) => a.attributes('href'))).toEqual(['https://example.com/docs', 'https://example.org']);
    expect(links.every((a) => a.attributes('target') === '_blank' && a.attributes('rel') === 'noopener noreferrer')).toBe(true);
  });
  it('renders block by block, so what is above the block being written stays as it was', async () => {
    const w = render('# Title\n\nfirst');
    const heading = w.find('h1').element;
    await w.setProps({ source: '# Title\n\nfirst and more' });
    expect(w.find('h1').element).toBe(heading);
    expect(w.findAll('.md-block')).toHaveLength(2);
  });
});

describe('Markdown math', () => {
  it('typesets TeX between each kind of mark, in a line and set apart', () => {
    const w = render('Inline $x^2$ and \\(y_1\\).\n\n$$\n\\frac{a}{b}\n$$\n\n\\[\nE = mc^2\n\\]');
    expect(w.findAll('p .katex')).toHaveLength(2);
    expect(w.findAll('.math-block.app-scroll.app-scroll-x .katex-display')).toHaveLength(2);
    expect(w.find('.math-block').text()).toContain('a');
  });

  it('leaves money as money, and math still being written as text', () => {
    expect(render('It costs $5 and $10, or $5-$10.').find('.katex').exists()).toBe(false);
    const open = render('$$\n\\frac{a}{b}');
    expect(open.find('.katex').exists()).toBe(false);
    expect(open.text()).toContain('$$');
  });

  it('keeps markdown out of a formula, and shows TeX it cannot read as code', () => {
    const w = render('$a*b*c$ and $\\frac{$');
    expect(w.find('em').exists()).toBe(false);
    expect(w.find('code.math-error').exists()).toBe(true);
  });
});

describe('Markdown media', () => {
  afterEach(() => allowedMedia.clear());

  it('asks before loading an image from another site, naming the site, and loads it when pressed', async () => {
    const w = render('![a cat](https://img.example.com/cat.png)');
    expect(w.find('img').exists()).toBe(false);
    const gate = w.find('button.media-gate');
    expect(gate.text()).toBe('Load image from img.example.com');
    await gate.trigger('click');
    const view = w.find('button.media-view');
    expect(view.attributes()).toMatchObject({ 'data-view': 'https://img.example.com/cat.png', 'data-alt': 'a cat', 'aria-label': 'Open image: a cat' });
    expect(view.find('img').attributes()).toMatchObject({ src: 'https://img.example.com/cat.png', alt: 'a cat', loading: 'lazy', referrerpolicy: 'no-referrer' });
  });

  it('loads at once what the reply holds itself, or anything when the reader chose so', () => {
    const png = 'data:image/png;base64,iVBORw0KGgo=';
    expect(render(`![dot](${png})`).find('.media-view img').attributes('src')).toBe(png);
    const w = withMedia('![clip](https://cdn.example.com/a.mp4) ![song](https://cdn.example.com/a.mp3) ![pic](https://cdn.example.com/a.webp)', 'always');
    expect(w.find('video').attributes()).toMatchObject({ src: 'https://cdn.example.com/a.mp4', controls: '', preload: 'metadata' });
    expect(w.find('audio').attributes('src')).toBe('https://cdn.example.com/a.mp3');
    expect(w.find('.media-view img').exists()).toBe(true);
  });

  it('names the kind of media waiting, and keeps an image inside a link the link’s', () => {
    expect(render('![clip](https://cdn.example.com/a.webm)').find('.media-gate').text()).toBe('Load video from cdn.example.com');
    const w = withMedia('[![logo](https://example.com/l.png)](https://example.com)', 'always');
    expect(w.find('a > img').exists()).toBe(true);
    expect(w.find('.media-view').exists()).toBe(false);
  });
});

describe('Markdown diagrams and previews', () => {
  afterEach(() => blockModes.clear());

  it('draws a closed mermaid block, keeps its code a press away, and leaves one being written as code', async () => {
    const w = render('```mermaid\ngraph TD; A-->B\n```');
    expect(w.find('.diagram.pending').exists()).toBe(true);
    await flushPromises();
    await flushPromises();
    expect(mermaid.render).toHaveBeenCalledWith(expect.stringMatching(/^hoshimi-mermaid-/), 'graph TD; A-->B\n');
    expect(mermaid.initialize).toHaveBeenCalledWith(expect.objectContaining({ securityLevel: 'strict', htmlLabels: false }));
    expect(w.find('.diagram-view svg').exists()).toBe(true);
    // As an image the drawing takes its viewBox's size: Mermaid states only a share of its box.
    expect(decodeURIComponent(w.find('.diagram-view').attributes('data-view')!)).toContain('<svg width="10" height="10" viewBox="0 0 10 10">');
    expect(w.find('pre').attributes('hidden')).toBeDefined();
    const toggle = w.find('.code-toggle');
    expect(toggle.text()).toBe('Code');
    await toggle.trigger('click');
    expect(w.find('pre').attributes('hidden')).toBeUndefined();
    expect(w.find('.diagram-view').exists()).toBe(false);
    expect(w.find('.code-toggle').text()).toBe('Diagram');

    const writing = render('```mermaid\ngraph TD; A-->');
    expect(writing.find('.code-toggle').exists()).toBe(false);
    expect(writing.find('pre').attributes('hidden')).toBeUndefined();
  });

  it('heads a diagram with its kind and its zoom, and keeps the drawing at its own size', async () => {
    const w = render('```mermaid\n%% a comment\nsequenceDiagram\n  A->>B: hi\n```');
    expect(w.find('.code-lang').text()).toBe('Mermaid');
    expect(w.find('.code-sub').text()).toBe('sequence');
    await flushPromises();
    await flushPromises();
    const tools = w.find('.code-head .code-tools');
    expect(tools.findAll('[data-zoom]').map((b) => b.attributes('aria-label'))).toEqual(['Zoom out', 'Actual size', 'Zoom in', 'Fit to window']);
    expect(tools.find('[data-open]').exists()).toBe(true);
    expect(tools.findAll('button').map((b) => b.classes()[0]).slice(-2)).toEqual(['code-toggle', 'code-copy']);
    expect(w.find('.diagram-frame').attributes()).toMatchObject({ 'data-width': '10', 'data-height': '10' });
    // Code shows no zoom: there is nothing to zoom.
    await w.find('.code-toggle').trigger('click');
    expect(w.find('[data-zoom]').exists()).toBe(false);
    expect(w.find('.code-sub').text()).toBe('sequence');
  });

  it('names the kind of a diagram by its first word, past front matter and comments', () => {
    expect(diagramType('graph TD; A-->B')).toBe('flowchart');
    expect(diagramType('---\ntitle: x\n---\n%% note\nstateDiagram-v2\n  [*] --> A')).toBe('state');
    expect(diagramType('somethingNew\n  a')).toBe('somethingNew');
  });

  it('takes Mermaid’s share of the box off a drawing, for its viewBox’s size', () => {
    const { svg, width, height } = naturalSize('<svg id="m1" width="100%" xmlns="http://www.w3.org/2000/svg" style="max-width: 2995px;" viewBox="-50 -10 2995 613"><g/></svg>');
    expect([width, height]).toEqual([2995, 613]);
    expect(svg).toBe('<svg width="2995" height="613" id="m1" xmlns="http://www.w3.org/2000/svg" viewBox="-50 -10 2995 613"><g/></svg>');
    expect(naturalSize('<svg width="40px" height="20"></svg>')).toMatchObject({ width: 40, height: 20 });
    expect(naturalSize('<svg><g/></svg>')).toMatchObject({ width: 0, height: 0 });
  });

  it('opens a wide diagram at a readable scale in a frame its height, and fits it whole when asked', async () => {
    vi.spyOn(HTMLElement.prototype, 'clientWidth', 'get').mockReturnValue(300);
    try {
      const w = render('```mermaid\ngraph LR; wide\n```');
      await flushPromises();
      await flushPromises();
      const frame = w.find('.diagram-frame');
      const svg = () => w.find('.diagram-view svg').element as SVGElement;
      // Fitting 1000 wide to 300 would be 30%: too small to read, so it opens at 60% and is dragged about.
      expect([svg().style.width, svg().style.height]).toEqual(['600px', '120px']);
      expect((w.find('.diagram').element as HTMLElement).style.height).toBe('120px');
      expect(w.find('[data-zoom="actual"]').text()).toBe('60%');
      expect(frame.classes()).toContain('pannable');
      await w.find('[data-zoom="fit"]').trigger('click');
      expect([svg().style.width, svg().style.height]).toEqual(['300px', '60px']);
      expect(w.find('[data-zoom="actual"]').text()).toBe('30%');
      expect(w.find('[data-zoom="out"]').attributes('disabled')).toBeDefined();
      expect(frame.classes()).not.toContain('pannable');
      await w.find('[data-zoom="actual"]').trigger('click');
      expect(svg().style.width).toBe('1000px');
      // Zoomed in, the frame grows with the drawing, up to its most.
      expect((w.find('.diagram').element as HTMLElement).style.height).toBe('200px');
      expect(frame.classes()).toContain('zoomed');
    } finally {
      vi.restoreAllMocks();
    }
  });

  it('zooms a drawn diagram with its buttons or Ctrl and the wheel, laid out anew, and leaves a plain wheel to the page', async () => {
    const w = render('```mermaid\ngraph LR; zoom-->me\n```');
    await flushPromises();
    await flushPromises();
    const frame = w.find('.diagram-frame');
    const svg = () => w.find('.diagram-view svg').element as SVGElement;
    expect(svg().style.width).toBe('10px');
    expect(w.find('[data-zoom="actual"]').text()).toBe('100%');
    await w.find('[data-zoom="in"]').trigger('click');
    expect([svg().style.width, svg().style.transform]).toEqual(['12.5px', '']);
    expect(w.find('[data-zoom="actual"]').text()).toBe('125%');
    expect(frame.classes()).toContain('zoomed');
    await w.find('[data-zoom="fit"]').trigger('click');
    expect(svg().style.width).toBe('10px');
    expect(frame.classes()).not.toContain('zoomed');

    const plain = new WheelEvent('wheel', { deltaY: -50, bubbles: true, cancelable: true });
    svg().dispatchEvent(plain);
    expect(plain.defaultPrevented).toBe(false);
    expect(svg().style.width).toBe('10px');
    const pinch = new WheelEvent('wheel', { deltaY: -50, bubbles: true, cancelable: true });
    // happy-dom's WheelEvent drops the modifier keys it is given.
    Object.defineProperty(pinch, 'ctrlKey', { value: true });
    svg().dispatchEvent(pinch);
    expect(pinch.defaultPrevented).toBe(true);
    expect(svg().style.width).toMatch(/^13\.4/);
  });

  it('keeps how a diagram was zoomed when its block is drawn again', async () => {
    const w = render('```mermaid\ngraph LR; keep-->me\n```');
    await flushPromises();
    await flushPromises();
    await w.find('[data-zoom="in"]').trigger('click');
    await w.setProps({ source: '```mermaid\ngraph LR; keep-->me\n```\n\nmore' });
    await w.find('.code-toggle').trigger('click');
    await w.find('.code-toggle').trigger('click');
    expect((w.find('.diagram-view svg').element as SVGElement).style.width).toBe('12.5px');
  });

  it('drags a diagram larger than its frame, and does not take the press that dragged it for a click', async () => {
    const opened = vi.fn();
    const w = mount(defineComponent({ setup: () => () => h('div', { onClick: opened }, [h(Markdown, { source: '```mermaid\ngraph LR; drag-->me\n```', copyLabel: 'Copy', copiedLabel: 'Copied' })]) }));
    await flushPromises();
    await flushPromises();
    const viewport = w.find('.diagram').element as HTMLElement;
    Object.defineProperty(viewport, 'clientWidth', { value: 5 });
    Object.defineProperty(viewport, 'scrollLeft', { value: 0, writable: true });
    await w.find('[data-zoom="in"]').trigger('click');
    expect(w.find('.diagram-frame').classes()).toContain('pannable');
    opened.mockClear();
    const view = w.find('.diagram-view');
    const at = (type: string, x: number) => view.element.dispatchEvent(new PointerEvent(type, { pointerId: 1, clientX: x, clientY: 0, bubbles: true }));
    at('pointerdown', 0);
    at('pointermove', 30);
    at('pointerup', 30);
    await view.trigger('click');
    expect(viewport.scrollLeft).toBe(-30);
    expect(opened).not.toHaveBeenCalled();
    await view.trigger('click');
    expect(opened).toHaveBeenCalledTimes(1);
    // The head's open button opens it too, through the drawing.
    await w.find('[data-open]').trigger('click');
    expect(opened).toHaveBeenCalledTimes(3);
  });

  it('says why a diagram could not be drawn, under its code', async () => {
    const w = render('```mermaid\nbroken diagram\n```');
    await flushPromises();
    await flushPromises();
    expect(w.find('.diagram-error').text()).toBe('Couldn’t draw this diagram: Parse error on line 1');
    expect(w.find('pre').attributes('hidden')).toBeUndefined();
  });

  it('shows an SVG block as an image, where its scripts never run', () => {
    const w = render('```svg\n<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script><circle r="4"/></svg>\n```');
    expect(w.find('script').exists()).toBe(false);
    expect(w.find('.svg-figure img').attributes('src')).toMatch(/^data:image\/svg\+xml;charset=utf-8,%3Csvg/);
    expect(w.find('.svg-figure .media-view').attributes('data-view')).toBe(w.find('.svg-figure img').attributes('src'));
  });

  it('previews an HTML block only when asked, in a frame with no origin and no network', async () => {
    const w = render('```html\n<h1>Hi</h1><script>document.title = 1</script>\n```');
    expect(w.find('iframe').exists()).toBe(false);
    expect(w.find('h1').exists()).toBe(false);
    await w.find('.code-toggle').trigger('click');
    const frame = w.find('iframe.html-preview');
    expect(frame.attributes('sandbox')).toBe('allow-scripts');
    expect(frame.attributes('srcdoc')).toContain("default-src 'none'");
    expect(frame.attributes('srcdoc')).toContain('<h1>Hi</h1>');
    expect(w.find('pre').attributes('hidden')).toBeDefined();
  });

});
