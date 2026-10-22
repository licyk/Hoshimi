import { describe, expect, it } from 'vitest';
import { COMMANDS, filterOptions, find, match, parseDraft, unavailable, type SlashContext } from '@/components/chat/slash/commands';

const idle: SlashContext = { busy: false, replied: true, kept: true, efforts: true, tools: true };

describe('slash commands', () => {
  it('reads a draft as a command only when it starts with a slash and names one word', () => {
    expect(parseDraft('/')).toEqual({ name: '', rest: '', spaced: false });
    expect(parseDraft('/mo')).toEqual({ name: 'mo', rest: '', spaced: false });
    expect(parseDraft('/model  gpt 5 ')).toEqual({ name: 'model', rest: 'gpt 5', spaced: true });
    expect(parseDraft('/compact keep\nthe names')).toEqual({ name: 'compact', rest: 'keep\nthe names', spaced: true });
    // A path, or a slash further in, is a message.
    expect(parseDraft('/etc/hosts')).toBeNull();
    expect(parseDraft('see /status')).toBeNull();
  });

  it('finds a command by its name or an alias, whatever the case', () => {
    expect(find('model')?.id).toBe('model');
    expect(find('APPROVAL')?.id).toBe('permissions');
    expect(find('clear')?.id).toBe('new');
    expect(find('nope')).toBeUndefined();
  });

  it('ranks exact names, then the ones begun, then the ones found inside, each in the menu order', () => {
    expect(match('').map((m) => m.command.id)).toEqual(COMMANDS.map((c) => c.id));
    expect(match('co').map((m) => m.command.id)).toEqual(['compact', 'status']);
    expect(match('co')[0].range).toEqual([1, 3]);
    expect(match('co')[1].range).toBeNull();
    expect(match('new').map((m) => m.command.id)).toEqual(['new']);
    // Found in an alias too: effort's "reasoning", status's "context".
    expect(match('o').map((m) => m.command.id)).toEqual(['model', 'effort', 'compact', 'status', 'fork', 'tools', 'permissions']);
    expect(match('o')[0].range).toEqual([2, 3]);
    // The reader's own words for a command find it too.
    expect(match('模型', (c) => (c.id === 'model' ? ['模型'] : [])).map((m) => m.command.id)).toEqual(['model']);
  });

  it('says why a command cannot run now', () => {
    expect(COMMANDS.every((c) => unavailable(c.id, idle) === null)).toBe(true);
    const busy = { ...idle, busy: true };
    expect(COMMANDS.filter((c) => unavailable(c.id, busy) === null).map((c) => c.id)).toEqual(['status', 'new', 'permissions']);
    expect(unavailable('compact', { ...idle, replied: false })).toBe('playground.slash.nothingToCompact');
    expect(unavailable('compact', { ...idle, replied: false, kept: false })).toBe('playground.slash.nothingYet');
    expect(unavailable('fork', { ...idle, kept: false })).toBe('playground.slash.nothingYet');
    expect(unavailable('effort', { ...idle, efforts: false })).toBe('playground.slash.noEfforts');
    expect(unavailable('permissions', { ...idle, tools: false })).toBe('playground.slash.noTools');
  });

  it('narrows a list by its labels, descriptions and keywords', () => {
    const options = [{ value: 'a', label: 'gpt-5', description: 'OpenAI' }, { value: 'b', label: 'glm-4.6', keywords: ['zhipu'] }];
    expect(filterOptions(options, '').length).toBe(2);
    expect(filterOptions(options, 'OPENAI').map((o) => o.value)).toEqual(['a']);
    expect(filterOptions(options, 'zhi').map((o) => o.value)).toEqual(['b']);
  });
});
