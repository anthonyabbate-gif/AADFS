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
  if (tab.dataset.tab === 'rankings') loadRankingSources();
  if (tab.dataset.tab === 'defense') loadDefense();
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


/* ───────────────────────── consensus rankings ───────────────────────── */
let rankingSourcesLoaded = false;

async function loadRankingSources() {
  if (rankingSourcesLoaded) return;
  try {
    const data = await api('/api/rankings/sources');
    const learned = data.learned_weights || {};
    $('#rk-sources').innerHTML = data.sources.map((s) => {
      const weight = learned[s.name];
      return `<div class="srccard">
        <div class="n">${escapeHtml(s.name)}
          <span class="kindtag">${escapeHtml(s.kind)}</span>
          ${s.available ? '' : '<span class="badge q">needs key</span>'}</div>
        <div class="d">${escapeHtml(s.description)}</div>
        ${weight !== undefined
          ? `<div class="d">learned weight <strong>${weight}</strong></div>` : ''}
      </div>`;
    }).join('');
    rankingSourcesLoaded = true;
  } catch (error) {
    $('#rk-sources').innerHTML = '';
  }
}

$('#rk-build').addEventListener('click', async () => {
  const button = $('#rk-build');
  button.disabled = true;
  notice('#rk-out', 'ok', '<span class="spinner"></span>Fetching every available source…');
  $('#rk-board').innerHTML = '';
  try {
    const data = await api('/api/rankings/build', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        season: Number($('#rk_season').value) || null,
        week: Number($('#rk_week').value) || null,
        refresh: $('#rk_refresh').checked,
        equal_weights: $('#rk_equal').checked,
      }),
    });
    renderBoard(data);
  } catch (error) {
    notice('#rk-out', 'err', escapeHtml(error.message));
  } finally {
    button.disabled = false;
  }
});

function renderBoard(data) {
  const ok = data.sources.filter((s) => s.ok && s.entries > 0);
  const bad = data.sources.filter((s) => !s.ok || s.entries === 0);

  const parts = [`<div class="notice ok">
    Built from <strong>${ok.length}</strong> source${ok.length === 1 ? '' : 's'}
    (${ok.map((s) => `${escapeHtml(s.source)} ${s.entries}`).join(', ')}).
    ${data.players.length} players ranked. Saved to
    <code>${escapeHtml(data.written.csv)}</code>.
  </div>`];
  if (bad.length) {
    parts.push(`<div class="notice warn"><strong>Unavailable this run:</strong>
      <ul>${bad.map((s) =>
        `<li>${escapeHtml(s.source)} — ${escapeHtml(s.error || 'no data')}</li>`).join('')}
      </ul></div>`);
  }
  if (ok.length === 1) {
    parts.push(`<div class="notice warn">Only one source came back, so there is no
      consensus to speak of and the tiers are evenly sized rather than meaningful.
      Add sources for this to be worth reading.</div>`);
  }
  $('#rk-out').innerHTML = parts.join('');

  const worstByPos = {};
  data.players.forEach((p) => {
    worstByPos[p.position] = Math.max(worstByPos[p.position] || 0, p.worst || 0);
  });

  $('#rk-board').innerHTML = data.positions.map((position) => {
    const players = data.players.filter((p) => p.position === position);
    const scale = worstByPos[position] || 1;
    let lastTier = null;
    const rows = players.map((p) => {
      const isBreak = lastTier !== null && p.tier !== lastTier;
      lastTier = p.tier;
      const left = ((p.best || 0) / scale) * 100;
      const width = Math.max(((p.range || 0) / scale) * 100, 2);
      const cls = { 'tight': 'tight', 'some spread': 'some', 'wide': 'wide',
                    'single source': 'single' }[p.agreement] || 'single';
      const detail = Object.entries(p.source_ranks)
        .sort((a, b) => a[1] - b[1])
        .map(([k, v]) => `${k}: ${v}`).join('\n');
      return `<tr class="${isBreak ? 'tier-break' : ''}">
        <td class="num">${p.rank}</td>
        <td><span class="tierbadge">T${p.tier}</span></td>
        <td>${escapeHtml(p.name)}
          ${p.outliers.length ? `<span class="badge q" title="${escapeHtml(p.outliers.join('; '))}">split</span>` : ''}</td>
        <td>${escapeHtml(p.team || '')}<span style="color:var(--muted)">${
          p.opponent ? ' vs ' + escapeHtml(p.opponent) : ''}</span></td>
        <td class="num">${p.consensus_rank}</td>
        <td><div class="spreadbar" title="best ${p.best}, worst ${p.worst}">
          <span style="left:${left}%;width:${width}%"></span></div></td>
        <td><span class="agree ${cls}">${escapeHtml(p.agreement)}</span></td>
        <td class="num" title="${escapeHtml(detail)}">${p.sources}</td>
        <td class="num">${p.mean_points !== null ? p.mean_points : ''}</td>
      </tr>`;
    }).join('');

    return `<div class="posblock">
      <h3>${escapeHtml(position)}<span class="count">${players.length} players</span></h3>
      <table>
        <thead><tr>
          <th class="num">#</th><th>Tier</th><th>Player</th><th>Team</th>
          <th class="num">Consensus</th><th>Range</th><th>Agreement</th>
          <th class="num">Src</th><th class="num">Pts</th>
        </tr></thead>
        <tbody>${rows}</tbody>
      </table>
    </div>`;
  }).join('');
}

loadRankingSources();


/* ───────────────────────── defence vs position ───────────────────────── */
let defenseData = null;

/* Map a value to one of nine diverging steps. The thresholds are in points per
   game above or below what an opponent normally scores, so the midpoint band is
   a genuine "no different from average" rather than a rank cut. */
function heatClass(value) {
  if (value === null || value === undefined) return 'h-0';
  const v = Number(value);
  if (v <= -6) return 'h-n4';
  if (v <= -4) return 'h-n3';
  if (v <= -2) return 'h-n2';
  if (v <= -0.75) return 'h-n1';
  if (v < 0.75) return 'h-0';
  if (v < 2) return 'h-p1';
  if (v < 4) return 'h-p2';
  if (v < 6) return 'h-p3';
  return 'h-p4';
}

/* The raw view has no natural zero, so it is centred on the league average. */
function heatClassRaw(allowed, average) {
  if (!average) return 'h-0';
  return heatClass(Number(allowed) - Number(average));
}

const tip = (() => {
  const el = document.createElement('div');
  el.className = 'heattip';
  document.body.appendChild(el);
  return el;
})();

function showTip(event, rating, average) {
  const trendWord = { tightening: 'tightening up', slipping: 'slipping',
                      steady: 'steady', 'n/a': 'not enough games' }[rating.trend];
  tip.innerHTML = `<div class="t">${escapeHtml(rating.team)} defence vs ${escapeHtml(rating.position)}</div>
    <dl>
      <dt>Allowed / game</dt><dd>${rating.allowed_per_game}</dd>
      <dt>League average</dt><dd>${average ?? '—'}</dd>
      <dt>Raw rank</dt><dd>${rating.rank} of ${rating.teams_ranked}</dd>
      <dt>Vs expected</dt><dd>${rating.above_expected > 0 ? '+' : ''}${rating.above_expected}</dd>
      <dt>Adjusted rank</dt><dd>${rating.adjusted_rank} of ${rating.teams_ranked}</dd>
      <dt>Last ${defenseData.recent_window}</dt><dd>${rating.recent_allowed ?? '—'}</dd>
      <dt>Trend</dt><dd>${escapeHtml(trendWord)}</dd>
      <dt>Games</dt><dd>${rating.games}</dd>
    </dl>
    <div style="margin-top:6px;color:var(--muted)">${escapeHtml(rating.label)}${
      rating.rank_shift && Math.abs(rating.rank_shift) >= 5
        ? ` — schedule moves this ${Math.abs(rating.rank_shift)} places`
        : ''}</div>`;
  tip.classList.add('on');
  const pad = 14;
  const rect = tip.getBoundingClientRect();
  let x = event.clientX + pad;
  let y = event.clientY + pad;
  if (x + rect.width > window.innerWidth - 8) x = event.clientX - rect.width - pad;
  if (y + rect.height > window.innerHeight - 8) y = event.clientY - rect.height - pad;
  tip.style.left = `${Math.max(8, x)}px`;
  tip.style.top = `${Math.max(8, y)}px`;
}
function hideTip() { tip.classList.remove('on'); }

$('#dv-load').addEventListener('click', () => loadDefense(true));

async function loadDefense(force) {
  if (defenseData && !force) return;
  notice('#dv-out', 'ok', '<span class="spinner"></span>Loading results and building the board…');
  $('#dv-matrix').innerHTML = '';
  $('#dv-matchups').innerHTML = '';
  const params = new URLSearchParams({
    season: $('#dv_season').value || '0',
    recent: $('#dv_recent').value || '4',
    refresh: $('#dv_refresh').checked ? 'true' : 'false',
  });
  try {
    defenseData = await api('/api/defense?' + params.toString());
    renderDefense();
  } catch (error) {
    notice('#dv-out', 'err', escapeHtml(error.message));
  }
}

$('#dv_metric').addEventListener('change', () => { if (defenseData) renderDefense(); });

function renderDefense() {
  const d = defenseData;
  const adjusted = $('#dv_metric').value === 'adjusted';
  const thin = d.ratings.some((r) => !r.reliable);

  notice('#dv-out', thin ? 'warn' : 'ok',
    `${d.season} through week <strong>${d.through_week}</strong>.
     League average allowed per game —
     ${d.positions.map((p) => `${p} <strong>${d.league_average[p] ?? '—'}</strong>`).join(', ')}.
     ${thin ? 'Some defences have played few games, so these ranks are still noisy.' : ''}`);

  const byTeam = {};
  d.ratings.forEach((r) => { (byTeam[r.team] = byTeam[r.team] || {})[r.position] = r; });

  const legend = `<div class="heatlegend">
    <span>tougher than average</span>
    <span class="swatches">
      ${['n4','n3','n2','n1','0','p1','p2','p3','p4']
        .map((k) => `<span class="sw" style="background:var(--heat-${k})"></span>`).join('')}
    </span>
    <span>more generous</span>
    <span style="margin-left:auto">each cell shows the ${adjusted ? 'adjusted' : 'raw'}
      rank; 1 = toughest of ${d.teams.length}</span>
  </div>`;

  const cellFor = (team, position) => {
    const r = (byTeam[team] || {})[position];
    if (!r) return '<td class="h-0">—</td>';
    const cls = adjusted
      ? heatClass(r.above_expected)
      : heatClassRaw(r.allowed_per_game, d.league_average[position]);
    const rank = adjusted ? r.adjusted_rank : r.rank;
    const sub = adjusted
      ? `${r.above_expected > 0 ? '+' : ''}${r.above_expected}`
      : `${r.allowed_per_game}`;
    return `<td class="${cls}${r.reliable ? '' : ' thin'}"
      data-team="${escapeHtml(team)}" data-pos="${escapeHtml(position)}">
      <span class="rk">${rank}</span><span class="ex">${sub}</span></td>`;
  };

  const tableFor = (teams) => `<table class="heat">
      <thead><tr><th></th>${d.positions.map((p) => `<th>vs ${p}</th>`).join('')}</tr></thead>
      <tbody>${teams.map((team) =>
        `<tr><th class="team">${escapeHtml(team)}</th>` +
        d.positions.map((p) => cellFor(team, p)).join('') + '</tr>').join('')}</tbody>
    </table>`;

  const half = Math.ceil(d.teams.length / 2);
  $('#dv-matrix').innerHTML = `<div class="heatwrap">
    ${legend}
    <div class="heatcols">
      ${tableFor(d.teams.slice(0, half))}
      ${tableFor(d.teams.slice(half))}
    </div>
  </div>`;

  // Per-cell hover, per the chart interaction rules.
  $('#dv-matrix').querySelectorAll('td[data-team]').forEach((cell) => {
    const r = byTeam[cell.dataset.team][cell.dataset.pos];
    cell.addEventListener('mousemove', (e) => showTip(e, r, d.league_average[r.position]));
    cell.addEventListener('mouseleave', hideTip);
  });

  renderMatchups(adjusted);
}

function renderMatchups(adjusted) {
  const d = defenseData;
  if (!d.matchups || !d.matchups.length) { $('#dv-matchups').innerHTML = ''; return; }

  const side = (team, faces) => {
    const cells = d.positions.map((p) => {
      const r = faces[p];
      if (!r) return `<span class="c h-0">${p} —</span>`;
      const cls = adjusted
        ? heatClass(r.above_expected)
        : heatClassRaw(r.allowed_per_game, d.league_average[p]);
      const rank = adjusted ? r.adjusted_rank : r.rank;
      return `<span class="c ${cls}" title="${escapeHtml(team)} ${p}s face ${escapeHtml(r.team)}: ${escapeHtml(r.label)}">${p} ${rank}</span>`;
    }).join('');
    return `<div class="mside"><span class="tm">${escapeHtml(team)}</span>
      <span class="cells">${cells}</span></div>`;
  };

  const games = d.matchups.map((m) => `<div class="matchup">
    <h4>${escapeHtml(m.away)} @ ${escapeHtml(m.home)}
      ${m.kickoff ? `<span style="color:var(--muted);font-weight:400"> · ${escapeHtml(m.kickoff)}</span>` : ''}</h4>
    ${side(m.away, m.faces[m.away] || {})}
    ${side(m.home, m.faces[m.home] || {})}
  </div>`).join('');

  $('#dv-matchups').innerHTML = `<div class="heat"><h3 style="margin:18px 0 8px">Week ${d.week} —
    the defence each offence faces</h3>
    <p class="hint" style="margin:0 0 12px">Higher number and warmer colour mean a
      softer matchup for that position group.</p>
    <div class="mgrid">${games}</div></div>`;
}
