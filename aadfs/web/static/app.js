'use strict';

const state = {
  players: [],
  locked: new Set(),
  excluded: new Set(),
  sort: { key: 'projection', dir: -1 },
};

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => Array.from(document.querySelectorAll(sel));
const money = (n) => '$' + Number(n).toLocaleString();
const pct = (n) => (Number(n) * 100).toFixed(1) + '%';

function notice(target, kind, html) {
  $(target).innerHTML = `<div class="notice ${kind}">${html}</div>`;
}

function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>"']/g, (c) => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]
  ));
}

async function api(url, options) {
  const response = await fetch(url, options);
  const text = await response.text();
  let payload;
  try { payload = text ? JSON.parse(text) : {}; } catch { payload = { detail: text }; }
  if (!response.ok) throw new Error(payload.detail || `Request failed (${response.status})`);
  return payload;
}

/* ───────────────────────── tabs ───────────────────────── */
$$('.tab').forEach((tab) => tab.addEventListener('click', () => {
  $$('.tab').forEach((t) => t.classList.remove('active'));
  $$('.panel').forEach((p) => p.classList.remove('active'));
  tab.classList.add('active');
  $('#tab-' + tab.dataset.tab).classList.add('active');
  if (tab.dataset.tab === 'players') renderPlayers();
  if (tab.dataset.tab === 'results') loadResults();
}));

/* ───────────────────────── slate upload ───────────────────────── */
$('#slate-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  const salaryInput = $('#salary_file');
  if (!salaryInput.files.length) return;

  const form = new FormData();
  form.append('salary_file', salaryInput.files[0]);
  Array.from($('#projection_files').files).forEach((f) => form.append('projection_files', f));
  form.append('season', $('#season').value || '0');
  form.append('week', $('#week').value || '0');
  form.append('history_seasons', $('#history_seasons').value || '');

  notice('#setup-out', 'ok', '<span class="spinner"></span>Loading slate and historical variance…');
  try {
    const data = await api('/api/slate/upload', { method: 'POST', body: form });
    renderSetup(data);
    $('#build-btn').disabled = false;
    $('#fetch-sources').disabled = false;
    $('#slate-chip').className = 'chip ok';
    $('#slate-chip').textContent =
      `${data.slate.season} Wk ${data.slate.week} · ${data.slate.players} players · ${data.slate.games.length} games`;
    await loadPlayers();
  } catch (error) {
    notice('#setup-out', 'err', escapeHtml(error.message));
  }
});

$('#fetch-sources').addEventListener('click', async () => {
  notice('#setup-out', 'ok', '<span class="spinner"></span>Fetching projection sources…');
  try {
    const data = await api('/api/projections/fetch', { method: 'POST' });
    renderSetup({ consensus: data });
    await loadPlayers();
  } catch (error) {
    notice('#setup-out', 'err', escapeHtml(error.message));
  }
});

function renderSetup(data) {
  const consensus = data.consensus || {};
  const parts = [];

  const ok = (consensus.sources || []).filter((s) => s.ok && s.matched > 0);
  const bad = (consensus.sources || []).filter((s) => !s.ok || s.matched === 0);

  parts.push(`<div class="notice ${ok.length ? 'ok' : 'warn'}">
    <strong>${consensus.projected ?? 0}</strong> players have a projection.
    ${consensus.missing_count ? `<strong>${consensus.missing_count}</strong> do not.` : ''}
    ${!ok.length ? ' No external source matched yet — FanDuel\'s own average is being used as a fallback.' : ''}
  </div>`);

  if (data.history_error) {
    parts.push(`<div class="notice warn">Historical variance unavailable, using position
      defaults. ${escapeHtml(data.history_error)}</div>`);
  }

  if (consensus.sources && consensus.sources.length) {
    parts.push('<div class="srcgrid">' + consensus.sources.map((s) => `
      <div class="srccard">
        <div class="n">${escapeHtml(s.source)} ${s.ok && s.matched ? '✓' : '✕'}</div>
        <div class="d">${s.ok
          ? `${s.matched} matched · ${s.unmatched} unmatched`
          : escapeHtml(s.error || 'failed')}</div>
        ${s.unmatched_sample && s.unmatched_sample.length
          ? `<div class="d">unmatched: ${escapeHtml(s.unmatched_sample.join(', '))}</div>` : ''}
      </div>`).join('') + '</div>');
  }

  if (consensus.missing && consensus.missing.length) {
    parts.push(`<div class="notice warn">No projection for:
      ${escapeHtml(consensus.missing.join(', '))}</div>`);
  }
  $('#setup-out').innerHTML = parts.join('');
}

/* ───────────────────────── players ───────────────────────── */
async function loadPlayers() {
  const data = await api('/api/players');
  state.players = data.players;
  renderPlayers();
  renderLocks();
}

$('#player-search').addEventListener('input', renderPlayers);
$('#pos-filter').addEventListener('change', renderPlayers);
$('#hide-unprojected').addEventListener('change', renderPlayers);
$('#clear-marks').addEventListener('click', () => {
  state.locked.clear();
  state.excluded.clear();
  renderPlayers();
  renderLocks();
});

$$('#player-table th[data-sort]').forEach((th) => th.addEventListener('click', () => {
  const key = th.dataset.sort;
  state.sort = { key, dir: state.sort.key === key ? -state.sort.dir : -1 };
  renderPlayers();
}));

function visiblePlayers() {
  const query = $('#player-search').value.trim().toLowerCase();
  const position = $('#pos-filter').value;
  const hide = $('#hide-unprojected').checked;
  let rows = state.players.filter((p) => {
    if (position && p.position !== position) return false;
    if (hide && !(p.projection > 0)) return false;
    if (query && !(`${p.name} ${p.team} ${p.opponent || ''}`.toLowerCase().includes(query))) return false;
    return true;
  });
  const { key, dir } = state.sort;
  rows.sort((a, b) => {
    const x = a[key], y = b[key];
    if (typeof x === 'string') return dir * x.localeCompare(y);
    return dir * ((x ?? 0) - (y ?? 0));
  });
  return rows;
}

function renderPlayers() {
  const body = $('#player-table tbody');
  const rows = visiblePlayers();
  if (!rows.length) {
    body.innerHTML = '<tr><td colspan="13" style="color:var(--muted)">No players to show.</td></tr>';
    return;
  }
  body.innerHTML = rows.map((p) => {
    const cls = state.locked.has(p.id) ? 'locked' : state.excluded.has(p.id) ? 'excluded' : '';
    const status = p.is_out
      ? '<span class="badge o">OUT</span>'
      : p.is_questionable ? `<span class="badge q">${escapeHtml(p.injury)}</span>` : '';
    const sourceTitle = Object.entries(p.source_values || {})
      .map(([k, v]) => `${k}: ${v}`).join('\n');
    return `<tr class="${cls}">
      <td>${escapeHtml(p.name)}</td>
      <td>${escapeHtml(p.position)}</td>
      <td>${escapeHtml(p.team)}<span style="color:var(--muted)"> vs ${escapeHtml(p.opponent || '')}</span></td>
      <td class="num">${money(p.salary)}</td>
      <td class="num">${p.projection.toFixed(1)}</td>
      <td class="num">${p.floor.toFixed(1)}</td>
      <td class="num">${p.ceiling.toFixed(1)}</td>
      <td class="num">${p.stdev.toFixed(1)}</td>
      <td class="num">${p.value.toFixed(2)}</td>
      <td class="num" title="${escapeHtml(sourceTitle)}">${p.sources}</td>
      <td>${status}</td>
      <td><input type="checkbox" data-lock="${escapeHtml(p.id)}" ${state.locked.has(p.id) ? 'checked' : ''}></td>
      <td><input type="checkbox" data-excl="${escapeHtml(p.id)}" ${state.excluded.has(p.id) ? 'checked' : ''}></td>
    </tr>`;
  }).join('');

  body.querySelectorAll('[data-lock]').forEach((box) => box.addEventListener('change', (e) => {
    const id = e.target.dataset.lock;
    if (e.target.checked) { state.locked.add(id); state.excluded.delete(id); }
    else state.locked.delete(id);
    renderPlayers(); renderLocks();
  }));
  body.querySelectorAll('[data-excl]').forEach((box) => box.addEventListener('change', (e) => {
    const id = e.target.dataset.excl;
    if (e.target.checked) { state.excluded.add(id); state.locked.delete(id); }
    else state.excluded.delete(id);
    renderPlayers(); renderLocks();
  }));
}

function nameOf(id) {
  const player = state.players.find((p) => p.id === id);
  return player ? player.name : id;
}

function renderLocks() {
  const parts = [];
  if (state.locked.size) {
    parts.push('Locked: ' + Array.from(state.locked)
      .map((id) => `<span>${escapeHtml(nameOf(id))}</span>`).join(''));
  }
  if (state.excluded.size) {
    parts.push('Excluded: ' + Array.from(state.excluded)
      .map((id) => `<span>${escapeHtml(nameOf(id))}</span>`).join(''));
  }
  $('#locks-summary').innerHTML = parts.join('<br>');
}

/* ───────────────────────── presets + sliders ───────────────────────── */
const PRESETS = {
  cash:     { weight_projection: 0.55, weight_floor: 0.45, weight_ceiling: 0,    risk_penalty: 0 },
  safe:     { weight_projection: 0.25, weight_floor: 0.75, weight_ceiling: 0,    risk_penalty: 0.3 },
  smallgpp: { weight_projection: 0.45, weight_floor: 0.15, weight_ceiling: 0.40, risk_penalty: 0 },
  proj:     { weight_projection: 1.0,  weight_floor: 0,    weight_ceiling: 0,    risk_penalty: 0 },
};

$('#preset').addEventListener('change', (e) => {
  const preset = PRESETS[e.target.value];
  Object.entries(preset).forEach(([id, value]) => { $('#' + id).value = value; });
  syncSliders();
});

const SLIDER_OUTPUTS = {
  weight_projection: '#wp-out', weight_floor: '#wf-out',
  weight_ceiling: '#wc-out', risk_penalty: '#rp-out',
};
function syncSliders() {
  Object.entries(SLIDER_OUTPUTS).forEach(([id, out]) => {
    $(out).textContent = Number($('#' + id).value).toFixed(2);
  });
}
Object.keys(SLIDER_OUTPUTS).forEach((id) =>
  $('#' + id).addEventListener('input', syncSliders));
syncSliders();

/* ───────────────────────── build ───────────────────────── */
$('#build-btn').addEventListener('click', async () => {
  const button = $('#build-btn');
  button.disabled = true;
  notice('#build-out', 'ok', '<span class="spinner"></span>Optimising and simulating…');
  $('#lineups').innerHTML = '';

  const payload = {
    n_lineups: Number($('#n_lineups').value),
    weight_projection: Number($('#weight_projection').value),
    weight_floor: Number($('#weight_floor').value),
    weight_ceiling: Number($('#weight_ceiling').value),
    risk_penalty: Number($('#risk_penalty').value),
    min_salary: Number($('#min_salary').value),
    max_per_game: $('#max_per_game').value ? Number($('#max_per_game').value) : null,
    min_sources: Number($('#min_sources').value),
    max_overlap: Number($('#max_overlap').value),
    avoid_dst_vs_own_offense: $('#avoid_dst_vs_own_offense').checked,
    exclude_out: $('#exclude_out').checked,
    exclude_questionable: $('#exclude_questionable').checked,
    simulate: $('#simulate').checked,
    cash_fraction: Number($('#cash_fraction').value),
    payout_multiple: Number($('#payout_multiple').value),
    locked: Array.from(state.locked),
    excluded: Array.from(state.excluded),
  };

  try {
    const data = await api('/api/build', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    renderLineups(data);
    // Stacked on a tablet, the lineups land far below the settings panel;
    // bring them into view rather than making you scroll to find them.
    if (window.matchMedia('(max-width: 900px)').matches) {
      $('#build-out').scrollIntoView({ behavior: 'smooth', block: 'start' });
    }
    $('#save-btn').disabled = false;
    $('#export-entries').classList.remove('disabled');
    $('#export-readable').classList.remove('disabled');
  } catch (error) {
    notice('#build-out', 'err', escapeHtml(error.message));
  } finally {
    button.disabled = false;
  }
});

$('#save-btn').addEventListener('click', async () => {
  try {
    const data = await api('/api/lineups/save', { method: 'POST' });
    notice('#build-out', 'ok', `Saved ${data.saved} lineup(s) for later comparison.`);
  } catch (error) {
    notice('#build-out', 'err', escapeHtml(error.message));
  }
});

function renderLineups(data) {
  const parts = [];
  if (data.relaxations && data.relaxations.length) {
    parts.push(`<div class="notice warn"><strong>Constraints were relaxed to find a lineup:</strong>
      <ul>${data.relaxations.map((r) => `<li>${escapeHtml(r)}</li>`).join('')}</ul></div>`);
  } else {
    parts.push(`<div class="notice ok">Built from a pool of ${data.pool_size} players.
      Break-even win rate at ${data.contest.payout_multiple}x is
      <strong>${pct(data.contest.breakeven_win_rate)}</strong>.</div>`);
  }
  $('#build-out').innerHTML = parts.join('');

  $('#lineups').innerHTML = data.lineups.map((lineup) => {
    const sim = lineup.simulation;
    const verdict = sim
      ? `<div class="verdict ${sim.beats_breakeven ? 'good' : 'bad'}">
           ${pct(sim.win_probability)} to cash · ${sim.expected_roi >= 0 ? '+' : ''}${pct(sim.expected_roi)} ROI
         </div>` : '';
    const simStats = sim ? `
      <div class="stat"><span class="k">Median</span><span class="v">${sim.median_score}</span></div>
      <div class="stat"><span class="k">10th pct</span><span class="v">${sim.p10_score}</span></div>
      <div class="stat"><span class="k">Cash line</span><span class="v">${sim.cash_line}</span></div>` : '';

    return `<div class="lineup">
      <div class="lineup-head">
        <span class="label">${escapeHtml(lineup.label)}</span>
        <div class="stat"><span class="k">Salary</span><span class="v">${money(lineup.salary)}</span></div>
        <div class="stat"><span class="k">Left</span><span class="v">${money(lineup.salary_remaining)}</span></div>
        <div class="stat"><span class="k">Proj</span><span class="v">${lineup.projection}</span></div>
        <div class="stat"><span class="k">Floor</span><span class="v">${lineup.floor}</span></div>
        <div class="stat"><span class="k">Games</span><span class="v">${lineup.games}</span></div>
        ${simStats}
        ${verdict}
      </div>
      <table>
        <tbody>${lineup.players.map((p) => `
          <tr>
            <td class="slot">${escapeHtml(p.slot)}</td>
            <td>${escapeHtml(p.name)}
              ${p.is_questionable ? `<span class="badge q">${escapeHtml(p.injury)}</span>` : ''}</td>
            <td>${escapeHtml(p.team)} <span style="color:var(--muted)">vs ${escapeHtml(p.opponent || '')}</span></td>
            <td class="num">${money(p.salary)}</td>
            <td class="num">${p.projection.toFixed(1)}</td>
            <td class="num" style="color:var(--muted)">${p.floor.toFixed(1)}–${p.ceiling.toFixed(1)}</td>
            <td class="num">${p.value.toFixed(2)}</td>
          </tr>`).join('')}
        </tbody>
      </table>
    </div>`;
  }).join('');
}

/* ───────────────────────── results ───────────────────────── */
$('#results-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  const form = new FormData();
  form.append('file', $('#results_file').files[0]);
  form.append('season', $('#r_season').value || '0');
  form.append('week', $('#r_week').value || '0');
  try {
    const data = await api('/api/results/upload', { method: 'POST', body: form });
    notice('#results-out', 'ok', `Imported ${data.parsed} entries (${data.new} new).`);
    await loadResults();
  } catch (error) {
    notice('#results-out', 'err', escapeHtml(error.message));
  }
});

async function loadResults() {
  let data;
  try { data = await api('/api/results/summary'); } catch { return; }
  const s = data.summary;
  if (!s.entries) return;

  const beating = s.cash_rate >= s.breakeven_cash_rate_at_1_8x;
  const kpis = `<div class="kpis">
    <div class="kpi"><div class="k">Entries</div><div class="v">${s.entries}</div></div>
    <div class="kpi"><div class="k">Cash rate</div>
      <div class="v ${beating ? 'good' : 'bad'}">${pct(s.cash_rate)}</div></div>
    <div class="kpi"><div class="k">Break-even needed</div>
      <div class="v">${pct(s.breakeven_cash_rate_at_1_8x)}</div></div>
    <div class="kpi"><div class="k">Net</div>
      <div class="v ${s.net >= 0 ? 'good' : 'bad'}">${s.net >= 0 ? '+' : ''}${money(s.net.toFixed(2))}</div></div>
    <div class="kpi"><div class="k">ROI</div>
      <div class="v ${s.roi >= 0 ? 'good' : 'bad'}">${s.roi >= 0 ? '+' : ''}${pct(s.roi)}</div></div>
  </div>`;

  const rows = (data.entries || []).slice(0, 60).map((e) => `<tr>
    <td>${escapeHtml(e.played_on || '')}</td>
    <td>${escapeHtml(e.contest_name || '')}</td>
    <td class="num">${e.score ?? ''}</td>
    <td class="num">${e.position ?? ''}${e.field_size ? ' / ' + e.field_size : ''}</td>
    <td class="num">${e.entry_fee != null ? money(e.entry_fee.toFixed(2)) : ''}</td>
    <td class="num" style="color:${(e.winnings || 0) > 0 ? 'var(--good)' : 'var(--muted)'}">
      ${e.winnings != null ? money(e.winnings.toFixed(2)) : ''}</td>
  </tr>`).join('');

  $('#results-out').innerHTML = kpis + `<div class="tablewrap"><table>
    <thead><tr><th>Date</th><th>Contest</th><th class="num">Score</th>
      <th class="num">Place</th><th class="num">Fee</th><th class="num">Won</th></tr></thead>
    <tbody>${rows}</tbody></table></div>`;
}
