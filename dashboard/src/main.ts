import {
  App,
  applyDocumentTheme,
  applyHostStyleVariables,
} from '@modelcontextprotocol/ext-apps';

interface MetricsSummary {
  projects?: number;
  total_facts: number;
  auto_learned_facts: number;
  fact_hits: number;
  prompt_injections: number;
  tool_hint_hits: number;
  attributed_hits: number;
  attribution_rate: number;
  loop_interventions: number;
  saved_loop_tokens: number;
  measured_loop_tokens?: number;
  tool_calls_total: number;
  tool_calls_failed: number;
  first_try_success_rate: number;
  tool_error_rate: number;
  command_calls_total: number;
  first_try_cmd_rate: number;
  saved_discovery_tokens: number;
  attributed_saved_tokens: number;
  injected_tokens: number;
  gross_saved_tokens: number;
  net_saved_tokens: number;
  attributed_net_saved_tokens: number;
  saved_usd: number;
  saved_cents: number;
  roi_multiplier: number;
  usd_per_mtok: number;
}

interface StatusInfo {
  active: boolean;
  mode: string;
  reflect: string;
  inject_mode: string;
  max_identical_calls: number;
  max_consecutive_errors: number;
  session_budget_tokens: number;
  session_budget_calls: number;
}

interface RecentEvent {
  ts?: string;
  event?: string;
  project?: string;
  agent?: string;
  fact_ids?: string[];
  tool?: string;
}

interface EventsSummary {
  total: number;
  agents: Record<string, Record<string, number>>;
  facts: Record<string, { shown: number; applied: number }>;
  recent?: RecentEvent[];
}

interface DashboardPayload {
  scope: 'project' | 'all';
  project_name: string;
  project_dir: string;
  storage_path: string;
  updated_at: string;
  metrics: MetricsSummary;
  status: StatusInfo;
  events: EventsSummary;
  error?: string;
}

const app = new App({ name: 'Centsible Live Dashboard', version: '1.0.0' }, {});

let currentScope: 'project' | 'all' = 'project';
let currentProjectDir = '';
let pollMs = 3000;
let pollingPaused = false;
let inFlight = false;
let timerId: number | null = null;
const historyPoints: number[] = [];

const $ = <T extends HTMLElement>(id: string): T =>
  document.getElementById(id) as T;

const fmtInt = (n: number | undefined): string =>
  Math.round(n ?? 0).toLocaleString();

function setPillState(state: 'live' | 'paused' | 'stale', text: string) {
  const pill = $('status-pill');
  pill.className = `pill ${state}`;
  $('status-text').textContent = text;
}

function showError(msg: string | null) {
  const el = $('err-banner');
  if (!msg) {
    el.style.display = 'none';
    el.textContent = '';
    return;
  }
  el.style.display = 'block';
  el.textContent = msg;
}

function escapeHtml(raw: string): string {
  return raw
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function renderSparkline(values: number[]) {
  const svg = $('sparkline');
  if (values.length < 2) {
    svg.innerHTML = `<text x="4" y="22" fill="var(--text-tertiary)" font-size="10" font-family="var(--mono)">Collecting live poll samples...</text>`;
    return;
  }
  const w = 300;
  const h = 36;
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = max - min || 1;
  const pts = values.map((v, i) => {
    const x = (i / (values.length - 1)) * (w - 6) + 3;
    const y = h - 4 - ((v - min) / span) * (h - 8);
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  });
  svg.innerHTML = `
    <polyline fill="none" stroke="var(--ok)" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" points="${pts.join(' ')}" />
  `;
}

function renderDashboard(data: DashboardPayload) {
  if (!data || !data.metrics) return;
  showError(data.error || null);

  if (data.scope) currentScope = data.scope;
  if (data.project_dir && !currentProjectDir) currentProjectDir = data.project_dir;

  $('btn-scope-project').classList.toggle('active', currentScope === 'project');
  $('btn-scope-all').classList.toggle('active', currentScope === 'all');

  const m = data.metrics;
  const s = data.status;
  const ev = data.events || { total: 0, agents: {}, facts: {}, recent: [] };

  $('scope-badge').textContent =
    currentScope === 'all'
      ? `all projects (${m.projects ?? 0})`
      : data.project_name || 'current project';

  // KPI 1: Tokens Saved
  const verifiedNet = m.attributed_net_saved_tokens ?? m.net_saved_tokens;
  const showTokens =
    (m.attributed_hits > 0 || m.fact_hits === 0) ? verifiedNet : m.net_saved_tokens;
  $('kpi-saved-tokens').textContent = `${showTokens >= 0 ? '+' : ''}${fmtInt(showTokens)}`;
  $('kpi-roi').textContent = `${m.roi_multiplier ?? 0}x ROI`;
  $('kpi-saved-usd').textContent =
    `${(m.saved_cents ?? 0).toFixed(2)}¢ / $${(m.saved_usd ?? 0).toFixed(4)} @ $${(m.usd_per_mtok ?? 3).toFixed(2)}/1M`;

  // KPI 2: 1st-Try Rate
  $('kpi-first-try').textContent = `${(m.first_try_success_rate ?? 100).toFixed(1)}%`;
  $('kpi-calls').textContent = `${fmtInt(m.tool_calls_total)} calls`;
  $('kpi-cmd-rate').textContent =
    m.command_calls_total > 0
      ? `Cmds: ${m.first_try_cmd_rate.toFixed(1)}% (${fmtInt(m.command_calls_total)}) · Err ${m.tool_error_rate.toFixed(1)}%`
      : `Tool error rate: ${(m.tool_error_rate ?? 0).toFixed(1)}%`;

  // KPI 3: Facts & Hits
  $('kpi-facts').textContent = `${fmtInt(m.total_facts)} facts`;
  $('kpi-attr-rate').textContent = `${(m.attribution_rate ?? 0).toFixed(1)}% applied`;
  $('kpi-hits').textContent =
    `${fmtInt(m.fact_hits)} hits (${fmtInt(m.attributed_hits)} verified · ${fmtInt(m.auto_learned_facts)} auto)`;

  // KPI 4: Loop Guard & Budget
  $('kpi-loops').textContent = `${fmtInt(m.loop_interventions)} stops`;
  $('kpi-mode').textContent = s?.mode ?? 'normal';
  const bParts: string[] = [];
  if (s?.session_budget_tokens) bParts.push(`${fmtInt(s.session_budget_tokens)} tok`);
  if (s?.session_budget_calls) bParts.push(`${fmtInt(s.session_budget_calls)} calls`);
  $('kpi-budget').textContent =
    bParts.length > 0
      ? `Budget: ${bParts.join(' / ')}`
      : `Loop saved: +${fmtInt(m.saved_loop_tokens)} tok`;

  // Token accounting bar
  const disc = Math.max(0, m.saved_discovery_tokens ?? 0);
  const loop = Math.max(0, m.saved_loop_tokens ?? 0);
  const over = Math.max(0, m.injected_tokens ?? 0);
  const sum = disc + loop + over || 1;
  $('bar-disc').style.width = `${((disc / sum) * 100).toFixed(1)}%`;
  $('bar-loop').style.width = `${((loop / sum) * 100).toFixed(1)}%`;
  $('bar-over').style.width = `${((over / sum) * 100).toFixed(1)}%`;

  $('acc-disc').textContent = `+${fmtInt(m.attributed_saved_tokens)} / +${fmtInt(m.saved_discovery_tokens)}`;
  $('acc-loop').textContent = `+${fmtInt(m.saved_loop_tokens)} tok`;
  $('acc-over').textContent = `-${fmtInt(m.injected_tokens)} tok`;
  $('acc-verified').textContent = `${verifiedNet >= 0 ? '+' : ''}${fmtInt(verifiedNet)} tok`;
  $('acc-guard').textContent = s
    ? `${s.mode} (${s.max_identical_calls} dup / ${s.max_consecutive_errors} err) · ${s.inject_mode}`
    : '-';

  historyPoints.push(verifiedNet);
  if (historyPoints.length > 24) historyPoints.shift();
  renderSparkline(historyPoints);
  $('poll-trend-label').textContent = `${historyPoints.length} samples`;

  // Agents & Events
  $('events-total').textContent = `${fmtInt(ev.total)} events`;
  const agentEntries = Object.entries(ev.agents || {});
  $('agent-summary').innerHTML = agentEntries.length
    ? agentEntries
        .map(([agent, counts]) => {
          const summary = Object.entries(counts)
            .sort(([a], [b]) => a.localeCompare(b))
            .map(([k, v]) => `${k}=${v}`)
            .join(', ');
          return `<div class="kv-row"><span class="kv-k">${escapeHtml(agent)}</span><span class="kv-v mono">${escapeHtml(summary)}</span></div>`;
        })
        .join('')
    : `<div class="empty">No agent events in the selected window.</div>`;

  const recent = ev.recent || [];
  $('recent-events').innerHTML = recent.length
    ? recent
        .slice(0, 12)
        .map((r) => {
          const kind = r.event || 'event';
          const tagCls = kind.includes('block')
            ? 'blocked'
            : kind === 'applied'
              ? 'applied'
              : kind.includes('warn')
                ? 'warn'
                : '';
          const detail = [
            r.project,
            r.tool,
            r.fact_ids?.length ? `facts: ${r.fact_ids.join(', ')}` : '',
          ]
            .filter(Boolean)
            .join(' · ');
          const tsShort = (r.ts || '').replace('T', ' ').replace('Z', '').slice(5, 19);
          return `
            <div class="event-item">
              <div class="event-left">
                <span class="event-tag ${tagCls}">${escapeHtml(kind)}</span>
                <span class="event-msg">${escapeHtml(detail || r.agent || '-')}</span>
              </div>
              <span class="event-ts mono">${escapeHtml(tsShort)}</span>
            </div>
          `;
        })
        .join('')
    : `<div class="empty">Waiting for tool / fact events...</div>`;

  // Facts table
  const factRows = Object.entries(ev.facts || {})
    .sort((a, b) => b[1].shown - a[1].shown)
    .slice(0, 15);
  $('facts-summary-note').textContent = `${factRows.length} active in window`;
  $('facts-tbody').innerHTML = factRows.length
    ? factRows
        .map(([fid, st]) => {
          const rate = st.shown > 0 ? ((st.applied / st.shown) * 100).toFixed(1) : '0.0';
          return `
            <tr>
              <td class="mono">${escapeHtml(fid)}</td>
              <td class="num">${fmtInt(st.shown)}</td>
              <td class="num">${fmtInt(st.applied)}</td>
              <td class="num">${rate}%</td>
            </tr>
          `;
        })
        .join('')
    : `<tr><td colspan="4" class="empty">No fact hits recorded in the last 7 days.</td></tr>`;

  $('footer-storage').textContent = `Storage: ${data.storage_path || '-'}`;
  const updatedTime = data.updated_at ? new Date(data.updated_at).toLocaleTimeString() : new Date().toLocaleTimeString();
  $('footer-updated').textContent = `Last poll: ${updatedTime}`;
}

async function fetchLatest() {
  if (inFlight) return;
  const caps = app.getHostCapabilities();
  if (caps && !caps.serverTools) {
    setPillState('stale', 'No serverTools');
    return;
  }
  inFlight = true;
  try {
    const res = await app.callServerTool({
      name: 'get_dashboard_data',
      arguments: {
        scope: currentScope,
        project_dir: currentProjectDir || undefined,
        days: 7,
      },
    });
    if (res?.structuredContent) {
      renderDashboard(res.structuredContent as unknown as DashboardPayload);
      if (!pollingPaused) setPillState('live', `Live · ${pollMs / 1000}s`);
    }
  } catch (err) {
    setPillState('stale', 'Stale');
    showError(err instanceof Error ? err.message : String(err));
  } finally {
    inFlight = false;
  }
}

function restartPolling() {
  if (timerId !== null) {
    window.clearInterval(timerId);
    timerId = null;
  }
  if (!pollingPaused) {
    setPillState('live', `Live · ${pollMs / 1000}s`);
    timerId = window.setInterval(fetchLatest, pollMs);
  } else {
    setPillState('paused', 'Paused');
  }
}

function wireControls() {
  $('btn-scope-project').addEventListener('click', () => {
    currentScope = 'project';
    fetchLatest();
  });
  $('btn-scope-all').addEventListener('click', () => {
    currentScope = 'all';
    fetchLatest();
  });
  $('interval-select').addEventListener('change', (e) => {
    pollMs = Number((e.target as HTMLSelectElement).value) || 3000;
    restartPolling();
  });
  $('btn-toggle-poll').addEventListener('click', () => {
    pollingPaused = !pollingPaused;
    $('btn-toggle-poll').textContent = pollingPaused ? 'Resume' : 'Pause';
    restartPolling();
  });
  $('btn-refresh').addEventListener('click', () => {
    fetchLatest();
  });
}

// Register handlers BEFORE connect()
app.ontoolinput = ({ arguments: args }) => {
  if (args && (args.scope === 'all' || args.scope === 'project')) {
    currentScope = args.scope;
  }
  if (args && typeof args.project_dir === 'string') {
    currentProjectDir = args.project_dir;
  }
};

app.ontoolresult = (result) => {
  if (result?.structuredContent) {
    renderDashboard(result.structuredContent as unknown as DashboardPayload);
    if (!pollingPaused) setPillState('live', `Live · ${pollMs / 1000}s`);
  }
};

app.onhostcontextchanged = (ctx) => {
  if (ctx.theme) applyDocumentTheme(ctx.theme);
  if (ctx.styles?.variables) applyHostStyleVariables(ctx.styles.variables);
};

async function init() {
  wireControls();
  await app.connect();
  const ctx = app.getHostContext();
  if (ctx?.theme) applyDocumentTheme(ctx.theme);
  if (ctx?.styles?.variables) applyHostStyleVariables(ctx.styles.variables);
  restartPolling();
  // Immediate refresh in case tool launched from aux_pane/global without full payload
  fetchLatest();
}

init().catch((err) => {
  setPillState('stale', 'Error');
  showError(err instanceof Error ? err.message : String(err));
});
