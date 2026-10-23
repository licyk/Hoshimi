import { flushPromises, mount } from '@vue/test-utils';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { defineComponent, h } from 'vue';
import Markdown, { COPIED_MS, renderMarkdown } from '@/ui/Markdown.vue';
import { MARKDOWN_CONTEXT, allowedMedia, blockModes, type RemoteMedia } from '@/ui/markdownRich';

// Mermaid itself is large and draws with a real layout engine: here it answers with a fixed drawing.
const mermaid = vi.hoisted(() => ({
  initialize: vi.fn(),
  render: vi.fn(async (_id: string, source: string) => {
    if (source.includes('broken')) throw new Error('Parse error on line 1\nmore');
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
    expect(ts.find('.code-lang').text()).toBe('ts');
    expect(ts.find('code').classes()).toEqual(['hljs', 'language-ts']);
    expect(ts.find('.hljs-keyword').text()).toBe('const');
    expect(ts.find('.hljs-comment').text()).toBe('// one');
    expect(ts.find('code').text()).toBe('const x: number = 1; // one');
    expect(py.find('.hljs-string').text()).toBe('"s"');
    expect(other.find('code').classes()).toEqual(['hljs']);
    expect(other.find('code').element.innerHTML).toBe('if &lt;x&gt; then\n');
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

  it('zooms a drawn diagram in place with its buttons or Ctrl and the wheel, and leaves a plain wheel to the page', async () => {
    const w = render('```mermaid\ngraph LR; zoom-->me\n```');
    await flushPromises();
    await flushPromises();
    const frame = w.find('.diagram-frame');
    const svg = () => w.find('.diagram-view svg').element as SVGElement;
    expect(frame.findAll('[data-zoom]').map((b) => b.attributes('aria-label'))).toEqual(['Zoom out', 'Zoom in', 'Fit to window']);
    await frame.find('[data-zoom="in"]').trigger('click');
    expect(svg().style.transform).toBe('translate(0px, 0px) scale(1.5)');
    expect(frame.classes()).toContain('zoomed');
    await frame.find('[data-zoom="reset"]').trigger('click');
    expect(svg().style.transform).toBe('');
    expect(frame.classes()).not.toContain('zoomed');

    const plain = new WheelEvent('wheel', { deltaY: -50, bubbles: true, cancelable: true });
    svg().dispatchEvent(plain);
    expect(plain.defaultPrevented).toBe(false);
    expect(svg().style.transform).toBe('');
    const pinch = new WheelEvent('wheel', { deltaY: -50, bubbles: true, cancelable: true });
    // happy-dom's WheelEvent drops the modifier keys it is given.
    Object.defineProperty(pinch, 'ctrlKey', { value: true });
    svg().dispatchEvent(pinch);
    expect(pinch.defaultPrevented).toBe(true);
    expect(svg().style.transform).toMatch(/scale\(1\.3/);
  });

  it('drags a zoomed diagram, and does not take the press that dragged it for a click', async () => {
    const opened = vi.fn();
    const w = mount(defineComponent({ setup: () => () => h('div', { onClick: opened }, [h(Markdown, { source: '```mermaid\ngraph LR; drag-->me\n```', copyLabel: 'Copy', copiedLabel: 'Copied' })]) }));
    await flushPromises();
    await flushPromises();
    await w.find('[data-zoom="in"]').trigger('click');
    opened.mockClear();
    const view = w.find('.diagram-view');
    const at = (type: string, x: number) => view.element.dispatchEvent(new PointerEvent(type, { pointerId: 1, clientX: x, clientY: 0, bubbles: true }));
    at('pointerdown', 0);
    at('pointermove', 30);
    at('pointerup', 30);
    await view.trigger('click');
    expect((w.find('.diagram-view svg').element as SVGElement).style.transform).toBe('translate(30px, 0px) scale(1.5)');
    expect(opened).not.toHaveBeenCalled();
    await view.trigger('click');
    expect(opened).toHaveBeenCalledTimes(1);
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
