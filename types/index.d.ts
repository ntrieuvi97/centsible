export type Metrics = Record<string, number>
export type Ev = { ts: string; event: string; project: string; tool: string; decision: string }
export type View = {
  metrics: Metrics
  mode: string
  events: Ev[]
  err: string
}

declare module 'claude-code' {
  interface PluginState {
    centsible: { view: Shaped<View> }
  }
}
