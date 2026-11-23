import { atom, read, update } from 'claude-code'
import type { Register } from 'claude-code'

import type { Ev, View } from '../types'

const PANE = 'centsible'
// Change the tag when View's fields change: it declines what an earlier version stored.
const view = atom(
  { plugin: 'centsible', key: 'view' } as const,
  { metrics: {}, mode: '?', events: [], err: '' } as View,
  { shape: 'view-1' },
)

let isPolling = false
const TAIL = 48 * 1024 // shortcut: tail of events.jsonl only, fine for a recent-events list

// shortcut: `$` typed any, tsc not available here; tighten once the engine lays its types
async function load($: any): Promise<View> {
  try {
    // Same resolution as core/event_log.py: $CENTSIBLE_HOME, else ~/.centsible.
    const dir =
      (await $.env.get('CENTSIBLE_HOME')) ||
      `${(await $.env.get('HOME')) || (await $.env.get('USERPROFILE'))}/.centsible`
    const metrics = JSON.parse(await $.fs.read(`${dir}/metrics.json`))
    const cfg = await $.fs.read(`${dir}/config.json`).then(JSON.parse).catch(() => ({}))
    const { size } = await $.fs.stat(`${dir}/events.jsonl`)
    const offset = Math.max(0, size - TAIL)
    const raw: string = await $.fs.read(`${dir}/events.jsonl`, { offset, length: size - offset })
    const lines = raw.split('\n').slice(offset > 0 ? 1 : 0)
    const events: Ev[] = []
    for (const l of lines) {
      try {
        const o = JSON.parse(l)
        events.push({ ts: o.ts ?? '', event: o.event ?? '', project: o.project ?? '', tool: o.tool ?? '', decision: o.decision ?? '' })
      } catch {}
    }
    return { metrics, mode: cfg.mode ?? '?', events: events.slice(-100), err: '' }
  } catch (e) {
    return { metrics: {}, mode: '?', events: [], err: String(e) }
  }
}

const n = (x: number | undefined) => (x ?? 0).toLocaleString('en-US')

export const register: Register = on => {
  on('session.start', async ($, e, next) => {
    await $.command.register({ name: 'cs-dashboard', description: 'Open the live Centsible dashboard pane' })

    return next(e)
  })

  on('command.run', { command: 'cs-dashboard' }, async $ => {
    // Opt-in: nothing is drawn or polled until a person runs the command.
    if (!isPolling) {
      isPolling = true
      const refresh = async () => {
        const v = await load($)
        await update($, view, () => v)
      }
      void refresh()
      // shortcut: polls for the rest of the session once opened, upgrade if idle cost matters
      $.clock.every(3000, refresh)
    }
    await $.ui.open({ id: PANE, title: 'Centsible' })

    return { text: 'Centsible dashboard opened.' }
  })

  on('ui.render', { component: 'Pane', requestId: PANE }, async ($, e) => {
    const { Box, Text } = $.ui.resolve(e)
    const v = await read($, view)
    const m = v.metrics
    const saved = (m.saved_discovery_tokens ?? 0) + (m.saved_loop_tokens ?? 0)
    const net = saved - (m.injected_tokens ?? 0)
    const total = m.tool_calls_total ?? 0
    const first = total ? Math.round(((m.first_try_successes ?? 0) / total) * 100) : 0
    const room = Math.max(1, (e.viewport?.rows ?? 24) - 16)
    const row = (k: string, val: string) => (
      <Text>
        {k.padEnd(22)}
        {val}
      </Text>
    )

    return (
      <Box flexDirection="column">
        {v.err !== '' && <Text color="red">{v.err}</Text>}
        <Text bold>Centsible · mode {v.mode}</Text>
        <Text> </Text>
        {row('Net tokens saved', n(net))}
        {row('  discovery saved', n(m.saved_discovery_tokens))}
        {row('  loop saved', n(m.saved_loop_tokens))}
        {row('  injected (cost)', n(m.injected_tokens))}
        {row('Loop interventions', n(m.loop_interventions))}
        {row('Fact hits', n(m.fact_hits))}
        {row('Tool-hint hits', n(m.tool_hint_hits))}
        {row('Auto-learned facts', n(m.auto_learned_facts))}
        {row('Tool calls', `${n(total)} (${first}% first try, ${n(m.tool_calls_failed)} failed)`)}
        <Text> </Text>
        <Text bold>Recent events</Text>
        {v.events.length === 0 && <Text dimColor>No events yet.</Text>}
        {v.events.slice(-room).reverse().map(ev => (
          <Text dimColor>
            {ev.ts.slice(11, 19)} {ev.event.padEnd(8)} {ev.project} {ev.tool} {ev.decision}
          </Text>
        ))}
      </Box>
    )
  })
}
