import { mount } from '@vue/test-utils';
import { describe, expect, it } from 'vitest';
import Markdown, { renderMarkdown } from '@/ui/Markdown.vue';

const render = (source: string) => mount(Markdown, { props: { source, copyLabel: 'Copy code' } });

describe('Markdown', () => {
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
    expect(w.find('.code-block > pre').classes()).toEqual(['app-scroll', 'app-scroll-auto']);
    expect(w.find('.table.app-scroll > table td').text()).toBe('1');
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

  it('reports a code block’s code, without its last newline, when its copy button is pressed', async () => {
    const w = render('Before\n\n```bash\npip install -U hoshimi\nhoshimi serve\n```\n\n    indented code\n');
    const buttons = w.findAll('button.code-copy');
    expect(buttons.map((b) => b.attributes('aria-label'))).toEqual(['Copy code', 'Copy code']);
    await buttons[0].trigger('click');
    await buttons[1].trigger('click');
    await w.find('p').trigger('click');
    expect(w.emitted('copy')).toEqual([['pip install -U hoshimi\nhoshimi serve'], ['indented code']]);
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
});
