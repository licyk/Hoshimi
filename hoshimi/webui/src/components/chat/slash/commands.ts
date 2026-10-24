import type { Component } from 'vue';
import { icons } from '@/ui';

/**
 * The Playground's slash commands: what typing ``/`` in the composer offers. The order is the
 * menu's, the most used first. A command either runs at once (``/new``), takes words after its name
 * (``/compact what to keep``, ``/fork its title``), or opens a list to choose from, which the words
 * after its name filter (``/model gpt``): the composer's text is the menu's only input, so the focus
 * never leaves it.
 */
export type SlashId = 'model' | 'effort' | 'compact' | 'status' | 'new' | 'fork' | 'permissions' | 'ps' | 'stop';
export type SlashPage = 'models' | 'efforts' | 'permissions' | 'status' | 'terminals' | 'stop';

export interface SlashCommand {
  id: SlashId;
  aliases: string[];
  icon: Component;
  /** Opens a list (or the status) rather than running at once. */
  page?: SlashPage;
  /** Takes the words after its name. */
  argument?: boolean;
}

export const COMMANDS: SlashCommand[] = [
  { id: 'model', aliases: ['m'], icon: icons.Cpu, page: 'models' },
  { id: 'effort', aliases: ['reasoning', 'think'], icon: icons.Brain, page: 'efforts' },
  { id: 'compact', aliases: ['summarize'], icon: icons.Layers, argument: true },
  { id: 'status', aliases: ['context', 'usage'], icon: icons.Gauge, page: 'status' },
  { id: 'new', aliases: ['clear'], icon: icons.SquarePen },
  { id: 'fork', aliases: ['branch'], icon: icons.GitBranch, argument: true },
  { id: 'permissions', aliases: ['approval', 'approvals'], icon: icons.Shield, page: 'permissions' },
  { id: 'ps', aliases: [], icon: icons.Terminal, page: 'terminals' },
  { id: 'stop', aliases: [], icon: icons.CircleStop, page: 'stop' },
];

/** What decides whether a command can run now. */
export interface SlashContext {
  /** A reply (or a compaction) is under way. */
  busy: boolean;
  /** The conversation has a reply since it was last compacted (something to compact). */
  replied: boolean;
  /** The conversation has messages kept (something to fork). */
  kept: boolean;
  /** The model has levels of effort to choose from. */
  efforts: boolean;
}

/** Why a command cannot run now (an i18n key), or null when it can. */
export function unavailable(id: SlashId, ctx: SlashContext): string | null {
  if (ctx.busy && !['status', 'permissions', 'new', 'ps', 'stop'].includes(id)) return 'playground.slash.busy';
  if ((id === 'compact' || id === 'fork') && !ctx.kept) return 'playground.slash.nothingYet';
  if (id === 'compact' && !ctx.replied) return 'playground.slash.nothingToCompact';
  if (id === 'effort' && !ctx.efforts) return 'playground.slash.noEfforts';
  return null;
}

/** The draft as a command: its name and the words after it; null when it is not one (it does not start with a slash, or the name runs over lines). */
export interface Draft {
  name: string;
  rest: string;
  /** A space follows the name: the words after it are the command's. */
  spaced: boolean;
}
export function parseDraft(text: string): Draft | null {
  const found = /^\/([^\s/]*)(\s+([\s\S]*))?$/.exec(text);
  if (!found) return null;
  return { name: found[1], rest: (found[3] ?? '').trim(), spaced: found[2] !== undefined };
}

/** A command by its name or one of its aliases. */
export function find(name: string): SlashCommand | undefined {
  const key = name.toLowerCase();
  return COMMANDS.find((c) => c.id === key || c.aliases.includes(key));
}

export interface Match {
  command: SlashCommand;
  /** The characters of ``/name`` the query matched, to show in bold: [start, end). */
  range: [number, number] | null;
}

/**
 * The commands ``query`` names: exact names (or aliases) first, then those it begins, then those
 * it is found in (in the name, an alias, or ``labels``, the words the reader's language has for
 * it), each in the menu's order. An empty query is every command.
 */
export function match(query: string, labels: (c: SlashCommand) => string[] = () => []): Match[] {
  const q = query.toLowerCase();
  if (!q) return COMMANDS.map((command) => ({ command, range: null }));
  const tiers: Match[][] = [[], [], []];
  for (const command of COMMANDS) {
    const names = [command.id, ...command.aliases];
    if (names.includes(q)) tiers[0].push({ command, range: command.id === q ? [1, q.length + 1] : null });
    else if (names.some((n) => n.startsWith(q))) tiers[1].push({ command, range: command.id.startsWith(q) ? [1, q.length + 1] : null });
    else if ([...names, ...labels(command).map((l) => l.toLowerCase())].some((n) => n.includes(q))) {
      const at = command.id.indexOf(q);
      tiers[2].push({ command, range: at >= 0 ? [at + 1, at + 1 + q.length] : null });
    }
  }
  return tiers.flat();
}

/** Options a list's words find: in the label, the description or the keywords, as the composer's menus search. */
export function filterOptions<T extends { label: string; description?: string; keywords?: string[] }>(options: T[], query: string): T[] {
  const q = query.trim().toLowerCase();
  if (!q) return options;
  return options.filter((o) => [o.label, o.description ?? '', ...(o.keywords ?? [])].some((s) => s.toLowerCase().includes(q)));
}
