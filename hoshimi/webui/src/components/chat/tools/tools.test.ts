import { flushPromises, mount } from '@vue/test-utils';
import { createPinia } from 'pinia';
import { describe, expect, it, vi } from 'vitest';
import type { ToolStep } from '@/api/types';
import ToolCall from '@/components/chat/ToolCall.vue';
import {
  numberLines, parseCommand, parseFetch, parsePatch, parseRead, parseSearch, partialString, prettyJson, terminalText, typedKeys,
} from '@/components/chat/tools/parse';
import { highlightLines, languageForPath } from '@/ui/highlight';

// The tools' own words, as hoshimi/core/agent writes them (taken from real calls).
const READ_PART = 'L2: x2 = 2\nL3: x3 = 3\nL4: x4 = 4\n[Lines 2–4 of 8. Read on with offset=5.]';
const READ_DIR = 'Directory /ws:\nsrc/\na.py\nempty.txt';
const SHELL_OK = 'Exit code: 0\nWall time: 0.1 seconds\nOutput:\nhi\nred\n';
const SHELL_TIMEOUT = 'Exit code: 124\nWall time: 1.0 seconds\nOutput:\ncommand timed out after 1000 milliseconds\n';
const SESSION_RUNNING = 'Wall time: 0.3 seconds\nProcess running with session ID 1\nOutput:\nhello\n';
const SESSION_DONE = 'Wall time: 0.1 seconds\nProcess exited with code 0\nOutput:\ndone\n';
const KILLED = 'Session 1 stopped (exit code -15).\nOutput:\n';
const SEARCH = 'Results for “vue diff”:\n\n1. Vue docs\n   https://vuejs.org/guide\n   The progressive framework.\n\n2. https://example.com\n   https://example.com\n';
const FETCH = 'URL: https://example.com/a\nTitle: Example\n\n# Hello\n\nSome **text**.\n\n[Cut at 10 of 99 characters.]';
const PATCH = [
  '*** Begin Patch',
  '*** Add File: src/new.py',
  '+print("hi")',
  '+x = 1',
  '*** Update File: app.py',
  '*** Move to: main.py',
  '@@ def greet():',
  '     name = 1',
  '-    print("Hi")',
  '+    print("Hello")',
  '+    print("again")',
  '@@',
  ' tail',
  '-gone',
  '*** Delete File: old.txt',
  '*** End Patch',
].join('\n');

describe('reading what the tools said', () => {
  it('reads a file’s numbered lines, with which they are of how many, and a long line’s cut', () => {
    expect(parseRead(READ_PART)).toEqual({
      kind: 'file', total: 8, next: 5,
      lines: [{ n: 2, text: 'x2 = 2', cut: 0 }, { n: 3, text: 'x3 = 3', cut: 0 }, { n: 4, text: 'x4 = 4', cut: 0 }],
    });
    const long = parseRead(`L8: ${'y'.repeat(500)}… [20 more characters]`);
    expect(long?.kind === 'file' && long.lines[0]).toEqual({ n: 8, text: 'y'.repeat(500), cut: 20 });
    // A line that only ends like a cut one keeps its words.
    expect(parseRead('L1: a… [2 more characters]')).toMatchObject({ lines: [{ text: 'a… [2 more characters]', cut: 0 }] });
    expect(parseRead('L1: \nL2: b')).toMatchObject({ lines: [{ n: 1, text: '' }, { n: 2, text: 'b' }] });
  });

  it('reads a listing, folders apart, an empty file, and gives up on anything else', () => {
    expect(parseRead(READ_DIR)).toEqual({ kind: 'dir', path: '/ws', more: 0, entries: [{ name: 'src', dir: true }, { name: 'a.py', dir: false }, { name: 'empty.txt', dir: false }] });
    expect(parseRead('Directory /e:\n(empty)')).toMatchObject({ entries: [] });
    expect(parseRead('Directory /d:\na\n[3 more entries not shown.]')).toMatchObject({ entries: [{ name: 'a' }], more: 3 });
    expect(parseRead('/ws/empty.txt is empty.')).toEqual({ kind: 'empty', path: '/ws/empty.txt' });
    expect(parseRead('No such file: /ws/nope')).toBeNull();
    expect(parseRead('L1: a\nnot a line')).toBeNull();
  });

  it('reads a patch into its files, their hunks and the lines they add and remove', () => {
    const [added, moved, deleted] = parsePatch(PATCH);
    expect(added).toMatchObject({ op: 'add', path: 'src/new.py', added: 2, removed: 0 });
    expect(moved).toMatchObject({ op: 'update', path: 'app.py', moveTo: 'main.py', added: 2, removed: 2 });
    expect(moved.hunks.map((h) => [h.header, h.lines.map((l) => l.kind[0] + l.text)])).toEqual([
      ['def greet():', ['c    name = 1', 'r    print("Hi")', 'a    print("Hello")', 'a    print("again")']],
      ['', ['ctail', 'rgone']],
    ]);
    expect(deleted).toMatchObject({ op: 'delete', path: 'old.txt', hunks: [] });
    // Still being written: as far as it has come.
    expect(parsePatch('*** Begin Patch\n*** Update File: a.py\n@@\n-x\n+y')).toMatchObject([{ path: 'a.py', added: 1, removed: 1 }]);
    // Lines before any @@ are a hunk of their own, as the tool reads them.
    expect(parsePatch('*** Begin Patch\n*** Update File: a\n a\n-b\n*** End Patch')[0].hunks).toHaveLength(1);
  });

  it('numbers a diff’s lines from where the tool says each hunk landed, and a new file’s from 1', () => {
    const [added, moved] = parsePatch(PATCH);
    const lines = numberLines(moved, [[10, 10], [40, 41]]).hunks.flatMap((h) => h.lines.map((l) => [l.old ?? null, l.new ?? null]));
    expect(lines).toEqual([[10, 10], [11, null], [null, 11], [null, 12], [40, 41], [41, null]]);
    expect(numberLines(added).hunks[0].lines.map((l) => l.new)).toEqual([1, 2]);
    // Starts that do not fit the hunks are not guessed at.
    expect(numberLines(moved, [[1, 1]]).hunks[0].lines[0].old).toBeUndefined();
  });

  it('reads each kind of command report', () => {
    expect(parseCommand(SHELL_OK)).toEqual({ exitCode: 0, wall: '0.1', timedOut: false, running: false, sessionId: null, output: 'hi\nred\n' });
    expect(parseCommand(SHELL_TIMEOUT)).toMatchObject({ exitCode: 124, timedOut: true, output: '' });
    expect(parseCommand(SESSION_RUNNING)).toMatchObject({ running: true, sessionId: 1, exitCode: null, output: 'hello\n' });
    expect(parseCommand(SESSION_DONE)).toMatchObject({ running: false, exitCode: 0, output: 'done\n' });
    expect(parseCommand(KILLED)).toMatchObject({ sessionId: 1, exitCode: -15, output: '' });
    expect(parseCommand('Unknown session ID 9; it may have exited and been read already.')).toBeNull();
    expect(parseCommand('Something new: 1\nOutput:\nx')).toBeNull();
  });

  it('shows terminal output as a screen would, and typed keys by name', () => {
    expect(terminalText('\x1b[31mred\x1b[0m\n10%\r50%\r100%\nok\r\n')).toBe('red\n100%\nok\n');
    expect(typedKeys('ls\n\x03\x1b')).toBe('ls⏎^C^[');
  });

  it('reads search results and a fetched page', () => {
    expect(parseSearch(SEARCH)).toEqual({
      query: 'vue diff',
      results: [
        { title: 'Vue docs', url: 'https://vuejs.org/guide', snippet: 'The progressive framework.' },
        { title: 'https://example.com', url: 'https://example.com', snippet: '' },
      ],
    });
    expect(parseSearch('No results for “zzz”.')).toEqual({ query: 'zzz', results: [] });
    expect(parseFetch(FETCH)).toEqual({ url: 'https://example.com/a', title: 'Example', body: '# Hello\n\nSome **text**.', cut: { at: 10, of: 99 } });
    expect(parseFetch('URL: https://x.org\n\nbody')).toMatchObject({ title: '', body: 'body', cut: null });
    expect(parseFetch('Could not fetch x')).toBeNull();
  });

  it('reads arguments still coming in, and JSON output', () => {
    expect(partialString('{"input": "*** Begin Patch\\n*** Add File: a\\n+\\"q\\" \\u00e9', 'input')).toBe('*** Begin Patch\n*** Add File: a\n+"q" é');
    expect(partialString('{"cmd": "ls", "workdir": "src"}', 'workdir')).toBe('src');
    expect(partialString('{"cmd": "l', 'workdir')).toBe('');
    expect(prettyJson('{"a":[1,2]}')).toBe('{\n  "a": [\n    1,\n    2\n  ]\n}');
    expect(prettyJson('plain text')).toBeNull();
  });

  it('highlights code as a whole and cuts it into lines that each close what they open', () => {
    const lines = highlightLines('x = """one\ntwo"""\ny = 1', 'python');
    expect(lines).toHaveLength(3);
    expect(lines[0]).toMatch(/<span class="hljs-string">&quot;&quot;&quot;one<\/span>$/);
    expect(lines[1]).toBe('<span class="hljs-string">two&quot;&quot;&quot;</span>');
    expect(highlightLines('<b>', '')).toEqual(['&lt;b&gt;']);
    expect([languageForPath('src/App.vue'), languageForPath('a/b.py'), languageForPath('Dockerfile'), languageForPath('notes')]).toEqual(['xml', 'python', 'dockerfile', '']);
  });
});

const step = (over: Partial<ToolStep>): ToolStep => ({
  type: 'tool', id: 'c1', name: 'shell', arguments: '{}', status: 'done', output: '', exit_code: null, ms: 0, meta: {}, approval: '', review: null, round: 0, ...over,
});
const opened = async (s: ToolStep) => {
  const w = mount(ToolCall, { props: { step: s }, global: { plugins: [createPinia()] } });
  await w.find('.line').trigger('click');
  return w;
};

describe('a tool call, opened', () => {
  it('shows a patch as each file’s diff, with its numbers once applied and its counts', async () => {
    const meta = {
      files: [
        { path: 'src/new.py', kind: 'add', added: 2, removed: 0 },
        { path: 'main.py', kind: 'update', from: 'app.py', added: 2, removed: 2, hunks: [[10, 10], [40, 41]] },
        { path: 'old.txt', kind: 'delete', added: 0, removed: 7 },
      ],
    };
    const w = await opened(step({ name: 'apply_patch', arguments: JSON.stringify({ input: PATCH }), meta, output: 'Success. Updated the following files:\nA src/new.py\nM app.py\nD old.txt\n' }));
    expect(w.findAll('.summary span').map((s) => s.text())).toEqual(['3 files changed', '+4', '−9']);
    const frames = w.findAll('.tool-frame');
    expect(frames.map((f) => [f.find('.code-lang').text(), f.find('.code-sub').text(), f.findAll('.code-tag').map((t) => t.text())])).toEqual([
      ['src/new.py', 'Added', ['+2']],
      ['main.py', 'Renamed from app.py', ['+2', '−2']],
      ['old.txt', 'Deleted', ['−7']],
    ]);
    const rows = frames[1].findAll('tr').map((r) => r.findAll('td').map((d) => d.text()));
    expect(rows.slice(0, 3)).toEqual([['⋯ def greet():'], ['10', '10', '', 'name = 1'], ['11', '', '−', 'print("Hi")']]);
    expect(frames[1].find('tr.add .hljs-built_in').exists()).toBe(true);
    expect(frames[2].find('.note').text()).toBe('Deleted, with its 7 lines.');
    // What the tool said of a patch that worked is the diff itself.
    expect(w.text()).not.toContain('Success.');
  });

  it('shows a patch as it is written, and why one could not be applied', async () => {
    const writing = await opened(step({ name: 'apply_patch', status: 'pending', arguments: '{"input": "*** Begin Patch\\n*** Update File: a.py\\n@@\\n-x\\n+y' }));
    expect(writing.find('.code-lang').text()).toBe('a.py');
    expect(writing.findAll('tr.add, tr.remove')).toHaveLength(2);
    const failed = await opened(step({ name: 'apply_patch', status: 'error', arguments: JSON.stringify({ input: PATCH }), output: 'apply_patch verification failed: Failed to find expected lines in app.py' }));
    expect(failed.find('.raw-output.failed').text()).toContain('Failed to find expected lines');
  });

  it('folds a long diff, and opens it on a press', async () => {
    const input = `*** Begin Patch\n*** Add File: big.txt\n${Array.from({ length: 200 }, (_, i) => `+${i}`).join('\n')}\n*** End Patch`;
    const w = await opened(step({ name: 'apply_patch', arguments: JSON.stringify({ input }) }));
    expect(w.find('table').exists()).toBe(false);
    await w.find('.fold').trigger('click');
    expect(w.findAll('tr')).toHaveLength(200);
  });

  it('shows a file read as its numbered lines, highlighted, and a directory as its entries', async () => {
    const file = await opened(step({ name: 'read', arguments: '{"path": "a.py", "offset": 2, "limit": 3}', output: READ_PART, meta: { path: '/ws/a.py', lines: 8 } }));
    expect(file.find('.code-lang').text()).toBe('/ws/a.py');
    expect(file.find('.code-sub').text()).toBe('Python');
    expect(file.find('.code-tag').text()).toBe('Lines 2–4 of 8');
    expect(file.findAll('.file-line').map((l) => [l.find('.n').text(), l.find('.text').text()])).toEqual([['2', 'x2 = 2'], ['3', 'x3 = 3'], ['4', 'x4 = 4']]);
    expect(file.find('.file-line .hljs-number').exists()).toBe(true);
    const dir = await opened(step({ name: 'read', arguments: '{"path": "."}', output: READ_DIR, meta: { path: '/ws', entries: 3 } }));
    expect(dir.findAll('.code-tag').map((t) => t.text())).toEqual(['1 folders', '2 files']);
    expect(dir.findAll('.entries li').map((li) => [li.classes('dir'), li.text()])).toEqual([[true, 'src/'], [false, 'a.py'], [false, 'empty.txt']]);
    const missing = await opened(step({ name: 'read', status: 'error', arguments: '{"path": "nope"}', output: 'No such file: /ws/nope' }));
    expect(missing.find('.raw-output.failed').text()).toBe('No such file: /ws/nope');
  });

  it('shows a command with its shell and where it ran, and its output under its exit code', async () => {
    const w = await opened(step({ name: 'shell', status: 'error', arguments: '{"command": "make\\nmake test"}', output: SHELL_TIMEOUT, exit_code: 124, meta: { cwd: '/ws', shell: 'bash', timed_out: true } }));
    const [asked, came] = w.findAll('.tool-frame');
    expect([asked.find('.code-lang').text(), asked.find('.code-sub').text(), asked.find('.code-tag').text()]).toEqual(['Command', 'bash', '/ws']);
    expect(asked.findAll('.cmd-line').map((l) => l.text())).toEqual(['$ make', 'make test']);
    expect(came.findAll('.code-tag').map((t) => [t.text(), t.classes()])).toEqual([
      ['Timed out', ['code-tag', 'bad']],
      ['Exit 124', ['code-tag', 'bad']],
      ['1.0 s', ['code-tag']],
    ]);
    expect(came.find('.note').text()).toBe('No output');
  });

  it('shows a background command running in its session, keys typed into it, and one stopped', async () => {
    const started = await opened(step({ name: 'exec_command', arguments: '{"cmd": "cat"}', output: SESSION_RUNNING, meta: { session_id: 1, running: true, shell: 'bash' } }));
    const out = started.findAll('.tool-frame')[1];
    expect([out.find('.code-sub').text(), out.findAll('.code-tag').map((t) => t.text())]).toEqual(['session 1', ['Running', '0.3 s']]);
    expect(out.find('.terminal').text()).toBe('hello');
    const typed = await opened(step({ name: 'write_stdin', arguments: '{"session_id": 1, "chars": "hello\\n"}', output: SESSION_RUNNING }));
    expect(typed.find('.keys').text()).toBe('hello⏎');
    const stopped = await opened(step({ name: 'kill_session', arguments: '{"session_id": 1}', output: KILLED }));
    expect(stopped.findAll('.tool-frame')).toHaveLength(1);
    expect(stopped.find('.code-tag').text()).toBe('Exit -15');
  });

  it('shows search results as links, and a fetched page as its text', async () => {
    const search = await opened(step({ name: 'web_search', arguments: '{"query": "vue diff"}', output: SEARCH }));
    const links = search.findAll('.results a');
    expect(links.map((a) => [a.text(), a.attributes('href'), a.attributes('rel')])).toEqual([
      ['Vue docs', 'https://vuejs.org/guide', 'noopener noreferrer'],
      ['https://example.com', 'https://example.com', 'noopener noreferrer'],
    ]);
    expect(search.find('.code-tag').text()).toBe('2 results');
    const page = await opened(step({ name: 'web_fetch', arguments: '{"url": "https://example.com/a"}', output: FETCH }));
    expect([page.find('.code-lang').text(), page.find('.code-sub').text(), page.find('.code-tag').text()]).toEqual(['Example', 'example.com/a', 'Cut short']);
    expect(page.find('.page h1').text()).toBe('Hello');
    expect(page.find('.page').classes()).not.toContain('whole');
    await page.findAll('.code-tools button').at(0)!.trigger('click');
    expect(page.find('.page').classes()).toContain('whole');
  });

  it('shows another tool’s arguments one to a row, and its JSON output highlighted', async () => {
    const w = await opened(step({ name: 'lookup', arguments: '{"id": 7, "note": "short", "filters": {"a": 1}}', output: '{"ok": true}' }));
    expect(w.findAll('.params .row').map((r) => [r.find('dt').text(), r.classes('block')])).toEqual([['id', false], ['note', false], ['filters', true]]);
    expect(w.find('.json .hljs-literal').text()).toBe('true');
    const plain = await opened(step({ name: 'lookup', arguments: 'not json', output: 'plain' }));
    expect(plain.findAll('.raw-output').map((p) => p.text())).toEqual(['not json', 'plain']);
  });

  it('copies what a frame holds from its head', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    vi.stubGlobal('navigator', { ...navigator, clipboard: { writeText } });
    try {
      const w = await opened(step({ name: 'read', arguments: '{"path": "a.py"}', output: READ_PART }));
      await w.find('.code-copy').trigger('click');
      await flushPromises();
      expect(writeText).toHaveBeenCalledWith('x2 = 2\nx3 = 3\nx4 = 4');
      expect(w.find('.code-copy').classes()).toContain('copied');
    } finally {
      vi.unstubAllGlobals();
    }
  });
});
