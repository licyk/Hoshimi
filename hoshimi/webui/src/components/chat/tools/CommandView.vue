<script setup lang="ts">
import { computed } from 'vue';
import CodeFrame from '@/components/chat/tools/CodeFrame.vue';
import RawOutput from '@/components/chat/tools/RawOutput.vue';
import { parseCommand, partialString, terminalText, typedKeys } from '@/components/chat/tools/parse';
import { useI18n } from '@/i18n';
import { icons } from '@/ui';
import { highlightLines } from '@/ui/highlight';

/**
 * A command and what it printed (``shell``, ``exec_command``, ``write_stdin``, ``kill_session``). What
 * was asked: the command, highlighted in its shell's language, with the shell and where it ran; or
 * the keys typed into a session. What came back: its output as a terminal would show it, under the
 * exit code (or that it runs on, in its session), whether it timed out, and how long it took.
 */
const props = defineProps<{ name: string; raw: string; args: Record<string, unknown>; output: string; meta: Record<string, unknown>; failed: boolean }>();
const { t } = useI18n();

const str = (key: string) => (typeof props.args[key] === 'string' ? (props.args[key] as string) : partialString(props.raw, key));
const command = computed(() => (props.name === 'shell' ? str('command') : props.name === 'exec_command' ? str('cmd') : ''));
const shell = computed(() => (typeof props.meta.shell === 'string' && props.meta.shell) || str('shell'));
const POWERSHELL = /powershell|pwsh/i;
const commandLines = computed(() => highlightLines(command.value, POWERSHELL.test(shell.value) ? 'powershell' : /^cmd/i.test(shell.value) ? '' : 'bash'));
const cwd = computed(() => (typeof props.meta.cwd === 'string' && props.meta.cwd) || str('workdir'));
const keys = computed(() => (props.name === 'write_stdin' ? str('chars') : ''));
const session = computed(() => {
  const id = props.args.session_id ?? props.meta.session_id;
  return id === undefined || id === null ? '' : t('playground.toolSession', { id: String(id) });
});

const result = computed(() => (props.output ? parseCommand(props.output) : null));
const printed = computed(() => (result.value ? terminalText(result.value.output).replace(/\n+$/, '') : ''));
const exitTag = computed(() => {
  const r = result.value;
  if (!r) return null;
  if (r.running) return { tone: 'warn', text: t('playground.toolView.running') };
  return r.exitCode === null ? null : { tone: r.exitCode === 0 ? 'ok' : 'bad', text: t('playground.toolExit', { code: String(r.exitCode) }) };
});
</script>

<template>
  <CodeFrame v-if="command" :icon="icons.Terminal" :title="t('playground.toolView.command')" :sub="shell" :copy="command">
    <template v-if="cwd" #tags><span class="code-tag path" :title="cwd">{{ cwd }}</span></template>
    <pre class="command"><code class="code-mono"><span v-for="(line, i) in commandLines" :key="i" class="cmd-line"><span class="prompt" aria-hidden="true">{{ i === 0 ? '$ ' : '' }}</span><span v-html="line" /></span></code></pre>
  </CodeFrame>
  <CodeFrame v-else-if="keys" :icon="icons.Keyboard" :title="t('playground.toolView.input')" :sub="session" :copy="keys">
    <pre class="command"><code class="code-mono keys">{{ typedKeys(keys) }}</code></pre>
  </CodeFrame>
  <slot name="review" />
  <CodeFrame v-if="result" :icon="icons.SquareTerminal" :title="t('playground.toolView.output')" :sub="result.sessionId !== null ? t('playground.toolSession', { id: String(result.sessionId) }) : ''" :copy="printed">
    <template #tags>
      <span v-if="result.timedOut" class="code-tag bad">{{ t('playground.toolView.timedOut') }}</span>
      <span v-if="exitTag" class="code-tag" :class="exitTag.tone">{{ exitTag.text }}</span>
      <span v-if="result.wall" class="code-tag">{{ t('playground.toolView.seconds', { s: result.wall }) }}</span>
    </template>
    <pre v-if="printed" class="terminal app-scroll app-scroll-chain app-scroll-auto"><code class="code-mono">{{ printed }}</code></pre>
    <p v-else class="note type-body-small">{{ t('playground.toolView.noOutput') }}</p>
  </CodeFrame>
  <RawOutput v-else-if="output" :text="output" :failed="failed" />
</template>

<style scoped>
/* Commands and output wrap rather than scroll sideways; long output scrolls down. */
.command, .terminal { margin: 0; padding: var(--app-space-2) var(--app-space-3); white-space: pre-wrap; overflow-wrap: anywhere; }
.terminal { max-height: 320px; }
.cmd-line { display: grid; grid-template-columns: 2ch minmax(0, 1fr); }
.prompt { color: var(--md-sys-color-on-surface-variant); white-space: pre; user-select: none; }
.keys { letter-spacing: 0.02em; }
.code-tag.path { max-width: 240px; overflow: hidden; text-overflow: ellipsis; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-weight: 400; display: inline-block; line-height: 22px; }
.note { margin: 0; padding: var(--app-space-2) var(--app-space-3); color: var(--md-sys-color-on-surface-variant); font-style: italic; }
</style>
