'use strict';

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => Array.from(document.querySelectorAll(sel));

const state = {
  limit: 24,
  offset: 0,
  total: 0,
  criteria: [],
  eventSource: null,
  activeJob: null,
  jobLogLines: [],
  logHidden: false,
  chartMode: 'scrapes',
};

/* ---------------- helpers ---------------- */

const esc = (value) =>
  value === null || value === undefined
    ? ''
    : String(value).replace(/[&<>"']/g, (c) => ({
        '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
      }[c]));

const money = (value) =>
  value === null || value === undefined
    ? ''
    : '$' + Math.round(Number(value)).toLocaleString('en-SG');

const moneyPrecise = (value) =>
  value === null || value === undefined
    ? ''
    : '$' + Number(value).toLocaleString('en-SG', { maximumFractionDigits: 2 });

function shortTime(iso) {
  if (!iso) return '';
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? iso : date.toLocaleString();
}

function duration(startIso, endIso) {
  if (!startIso || !endIso) return '';
  const ms = new Date(endIso) - new Date(startIso);
  if (Number.isNaN(ms) || ms < 0) return '';
  const seconds = Math.round(ms / 1000);
  if (seconds < 60) return `${seconds}s`;
  return `${Math.floor(seconds / 60)}m ${seconds % 60}s`;
}

let toastTimer = null;
function toast(message, isError = false) {
  const node = $('#toast');
  node.textContent = message;
  node.classList.toggle('error', isError);
  node.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { node.hidden = true; }, isError ? 7000 : 3500);
}

async function api(path, options) {
  const response = await fetch(path, options);
  const isJson = (response.headers.get('content-type') || '').includes('application/json');
  const payload = isJson ? await response.json() : null;
  if (!response.ok) {
    const detail = payload && payload.detail ? payload.detail : `HTTP ${response.status}`;
    throw new Error(typeof detail === 'string' ? detail : JSON.stringify(detail));
  }
  return payload;
}

function post(path, body) {
  return api(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body || {}),
  });
}

/* ---------------- tabs ---------------- */

$$('.tab').forEach((tab) => {
  tab.addEventListener('click', () => {
    $$('.tab').forEach((other) => other.classList.toggle('is-active', other === tab));
    $$('.tab-panel').forEach((panel) => {
      panel.classList.toggle('is-active', panel.id === `tab-${tab.dataset.tab}`);
    });
    if (tab.dataset.tab === 'runs') loadRuns();
  });
});

/* ---------------- dashboard ---------------- */

async function loadOverview() {
  let data;
  try {
    data = await api('/api/overview');
  } catch (error) {
    $('#stats').innerHTML = `<div class="empty">Could not load stats: ${esc(error.message)}</div>`;
    return;
  }
  const s = data.stats;
  const tiles = [
    { label: 'Total listings', value: (s.total_listings || 0).toLocaleString() },
    { label: 'Scraped today', value: (data.scraped_today || 0).toLocaleString(), sub: data.today },
    { label: 'Awaiting evaluation', value: (data.unevaluated || 0).toLocaleString(), sub: `${data.scored_total || 0} scored so far` },
    { label: 'Average price', value: moneyPrecise(s.avg_price) || '—' },
    { label: 'Median price', value: moneyPrecise(s.median_price) || '—' },
    { label: 'Average PSF', value: s.avg_psf != null ? moneyPrecise(s.avg_psf) : '—' },
    { label: 'Price history rows', value: (s.price_changes || 0).toLocaleString() },
    {
      label: 'Last run',
      value: data.last_run ? `#${data.last_run.run_id}` : '—',
      sub: data.last_run ? `${data.last_run.kind} · ${data.last_run.status}` : 'no runs yet',
    },
  ];
  $('#stats').innerHTML = tiles
    .map(
      (t) => `<div class="stat">
        <div class="label">${esc(t.label)}</div>
        <div class="value">${esc(t.value)}</div>
        ${t.sub ? `<div class="sub">${esc(t.sub)}</div>` : ''}
      </div>`
    )
    .join('');

  const types = Object.entries(s.by_property_type || {});
  const max = Math.max(1, ...types.map(([, count]) => count));
  $('#types').innerHTML = types.length
    ? types
        .map(
          ([name, count]) => `<div class="type-row">
            <span>${esc(name)}</span>
            <span class="type-track"><span class="type-fill" style="width:${(count / max) * 100}%"></span></span>
            <span class="count">${count.toLocaleString()}</span>
          </div>`
        )
        .join('')
    : '<p class="meta">No listings yet — run a scrape to get started.</p>';
}

async function loadChart() {
  const days = Number($('#days-range').value);
  let data;
  try {
    data = await api(`/api/days?days=${days}`);
  } catch (error) {
    $('#chart').innerHTML = `<div class="empty">Could not load activity: ${esc(error.message)}</div>`;
    return;
  }
  const rows = data.days;
  const max = Math.max(1, ...rows.map((r) => Math.max(r.scrapes, r.agent_runs, r.failed)));
  const showTicks = rows.length <= 30;

  $('#chart').innerHTML = rows
    .map((row, index) => {
      const parts = [];
      const build = (count, cls) => {
        if (!count) return;
        const height = (count / max) * 100;
        parts.push(`<div class="bar ${cls}" style="height:${height}%" title="${count}"></div>`);
      };
      const byMode = state.chartMode === 'scrapes' ? row.scrapes : row.agent_runs;
      if (state.chartMode === 'scrapes') {
        build(row.failed, 'fail');
        build(row.scrapes - row.failed, 'eval');
      } else {
        build(byMode, 'eval');
      }
      const date = new Date(row.day + 'T00:00:00');
      const label = showTicks
        ? date.toLocaleDateString(undefined, { day: '2-digit', month: 'short' })
        : index % Math.ceil(rows.length / 8) === 0
          ? date.toLocaleDateString(undefined, { day: '2-digit', month: 'short' })
          : '';
      const value = state.chartMode === 'scrapes' ? row.scrapes : row.agent_runs;
      return `<div class="bar-col ${value ? 'has-value' : ''}" title="${row.day}: ${row.scrapes} scrape(s), ${row.agent_runs} evaluation(s), ${row.inserted} new listings">
        <span class="tick">${value || ''}</span>
        <div class="bar-stack">${parts.join('') || '<div class="bar" style="height:2px;opacity:.25"></div>'}</div>
        <span class="tick">${label}</span>
      </div>`;
    })
    .join('');
}

$('#days-range').addEventListener('change', loadChart);
$('#btn-chart-mode').addEventListener('click', (event) => {
  state.chartMode = state.chartMode === 'scrapes' ? 'agent' : 'scrapes';
  event.target.textContent = state.chartMode === 'scrapes' ? 'Scrapes' : 'Evaluations';
  loadChart();
});

/* ---------------- properties ---------------- */

const filters = {
  q: () => $('#f-q').value.trim(),
  district: () => $('#f-district').value,
  property_type: () => $('#f-type').value,
  outcome: () => $('#f-outcome').value,
  min_price: () => $('#f-min').value,
  max_price: () => $('#f-max').value,
  sort: () => $('#f-sort').value,
};

let filterTimer = null;
['#f-q', '#f-min', '#f-max'].forEach((sel) => {
  $(sel).addEventListener('input', () => {
    clearTimeout(filterTimer);
    filterTimer = setTimeout(() => { state.offset = 0; loadProperties(); }, 350);
  });
});
['#f-district', '#f-type', '#f-outcome', '#f-sort'].forEach((sel) => {
  $(sel).addEventListener('change', () => { state.offset = 0; loadProperties(); });
});
$('#btn-reset').addEventListener('click', () => {
  ['#f-q', '#f-district', '#f-type', '#f-outcome', '#f-min', '#f-max', '#f-sort'].forEach((sel) => {
    $(sel).value = sel === '#f-sort' ? 'newest' : '';
  });
  state.offset = 0;
  loadProperties();
});
$('#btn-prev').addEventListener('click', () => {
  state.offset = Math.max(0, state.offset - state.limit);
  loadProperties();
});
$('#btn-next').addEventListener('click', () => {
  if (state.offset + state.limit < state.total) {
    state.offset += state.limit;
    loadProperties();
  }
});

function cardHtml(p) {
  const image = p.image_url
    ? `<div class="card-img" style="background-image:url('${esc(p.image_url)}')">
         <span class="badge ${p.outcome || 'none'}">${p.outcome ? esc(p.outcome) : 'UNSCORED'}</span>
       </div>`
    : `<div class="card-img placeholder"><span class="badge ${p.outcome || 'none'}">${p.outcome ? esc(p.outcome) : 'UNSCORED'}</span>&#127968;</div>`;

  const facts = [];
  if (p.bedrooms !== null && p.bedrooms !== undefined) facts.push(`${p.bedrooms} bed`);
  if (p.bathrooms !== null && p.bathrooms !== undefined) facts.push(`${p.bathrooms} bath`);
  if (p.size_sqft) facts.push(`${p.size_sqft.toLocaleString()} sqft`);
  if (p.property_type) facts.push(esc(p.property_type));
  if (p.mrt) facts.push(esc(String(p.mrt).slice(0, 22)));

  const scoreBar = p.score !== null && p.score !== undefined
    ? `<div class="card-score">
         <span class="total">${Number(p.score).toFixed(2)}</span>
         <span class="segbar">${[1, 2, 3, 4, 5, 6, 7, 8]
           .map((i) => `<i class="seg s${p[`c${i}`] !== undefined ? p[`c${i}`] : 0}"></i>`)
           .join('')}</span>
       </div>`
    : '<div class="card-score"><span class="meta">Not yet evaluated</span></div>';

  return `<article class="card" data-id="${p.listing_id}">
    ${image}
    <div class="card-body">
      <div>
        <div class="card-price">${esc(money(p.price_value) || p.price || 'Price n/a')}
          <span class="card-psf">${esc(p.price_per_area || '')}</span>
        </div>
        <div class="card-title">${esc(p.title || 'Untitled')}</div>
        <div class="card-addr">${esc(p.address || p.district || 'Address n/a')}</div>
      </div>
      <div class="facts">${facts.map((f) => `<span class="fact">${f}</span>`).join('')}</div>
      ${scoreBar}
    </div>
  </article>`;
}

async function loadProperties() {
  const query = new URLSearchParams({ limit: state.limit, offset: state.offset });
  Object.entries(filters).forEach(([key, getter]) => {
    const value = getter();
    if (value !== '') query.set(key, value);
  });

  $('#props').innerHTML = '<div class="empty">Loading properties…</div>';
  let data;
  try {
    data = await api(`/api/properties?${query.toString()}`);
  } catch (error) {
    $('#props').innerHTML = `<div class="empty">Could not load properties: ${esc(error.message)}</div>`;
    return;
  }

  state.total = data.total;
  const from = data.total ? state.offset + 1 : 0;
  const to = Math.min(state.offset + state.limit, data.total);
  $('#props-meta').textContent = `Showing ${from}–${to} of ${data.total.toLocaleString()} properties`;
  $('#pager-label').textContent = `Page ${Math.floor(state.offset / state.limit) + 1} of ${Math.max(1, Math.ceil(data.total / state.limit))}`;
  $('#btn-prev').disabled = state.offset === 0;
  $('#btn-next').disabled = state.offset + state.limit >= data.total;

  $('#props').innerHTML = data.properties.length
    ? data.properties.map(cardHtml).join('')
    : '<div class="empty">No properties match these filters.</div>';

  $$('#props .card').forEach((card) => {
    card.addEventListener('click', () => openProperty(Number(card.dataset.id)));
  });

  syncFacets(data.facets);
}

function syncFacets(facets) {
  if (!facets) return;
  const fill = (sel, values, current) => {
    if ($(sel).dataset.filled === String(values.length)) return;
    $(sel).dataset.filled = String(values.length);
    const first = $(sel).options[0];
    $(sel).innerHTML = '';
    $(sel).appendChild(first);
    values.forEach((value) => {
      const option = document.createElement('option');
      option.value = value;
      option.textContent = value;
      if (value === current) option.selected = true;
      $(sel).appendChild(option);
    });
  };
  fill('#f-district', facets.districts || [], $('#f-district').value);
  fill('#f-type', facets.property_types || [], $('#f-type').value);
}

/* ---------------- property drawer ---------------- */

async function openProperty(listingId) {
  const drawer = $('#drawer');
  const content = $('#drawer-content');
  content.innerHTML = '<p class="meta">Loading…</p>';
  drawer.hidden = false;

  let data;
  try {
    data = await api(`/api/properties/${listingId}`);
  } catch (error) {
    content.innerHTML = `<p class="meta">Could not load property: ${esc(error.message)}</p>`;
    return;
  }

  const l = data.listing;
  const score = data.scores[0] || null;
  const images = (l.image_urls && l.image_urls.length ? l.image_urls : l.image_url ? [l.image_url] : []);

  const rows = [
    ['Listing ID', l.listing_id],
    ['Property type', l.property_type],
    ['Street', l.street],
    ['District', l.district],
    ['Bedrooms', l.bedrooms],
    ['Bathrooms', l.bathrooms],
    ['Size', l.size],
    ['PSF', l.price_per_area],
    ['Tenure', l.tenure],
    ['Built', l.build_year],
    ['MRT', l.mrt],
    ['Listed', l.recency || l.listed_date],
    ['First seen', shortTime(l.first_seen_at)],
    ['Last seen', shortTime(l.last_seen_at)],
    ['Agent', l.agent_name],
    ['Agency', l.agent_company],
    ['Photos', l.image_count],
  ];

  const hero = images[0]
    ? `<div class="drawer-hero" style="background-image:url('${esc(images[0])}')"></div>`
    : '<div class="drawer-hero placeholder">&#127968;</div>';

  let html = hero;
  if (images.length > 1) {
    html += `<div class="gallery">${images
      .map((src) => `<img src="${esc(src)}" alt="" loading="lazy">`)
      .join('')}</div>`;
  }

  html += `<h2>${esc(l.title || 'Untitled')}</h2>
    <div class="meta">${esc(l.address || 'Address n/a')}</div>
    <div class="price-lg">${esc(money(l.price_value) || l.price || 'Price n/a')}
      ${l.price_per_area ? `<span class="card-psf">${esc(l.price_per_area)}</span>` : ''}</div>
    <a href="${esc(l.url)}" target="_blank" rel="noopener">Open on PropertyGuru &rarr;</a>`;

  html += '<h3>Details</h3><dl class="kv">' +
    rows
      .filter(([, value]) => value !== null && value !== undefined && value !== '')
      .map(([key, value]) => `<dt>${esc(key)}</dt><dd>${esc(value)}</dd>`)
      .join('') +
    '</dl>';

  if (l.description) {
    html += `<h3>Description</h3><div class="desc">${esc(l.description)}</div>`;
  }

  if (data.price_history.length > 1) {
    html += `<h3>Price history</h3><table class="data">
      <tr><th>Seen</th><th>Price</th></tr>
      ${data.price_history
        .map(
          (row) => `<tr><td>${esc(shortTime(row.seen_at))}</td><td>${esc(money(row.price_value))}</td></tr>`
        )
        .join('')}
    </table>`;
  }

  if (score) {
    const evidence = (score.evidence && score.evidence.criteria) || {};
    html += `<h3>Evaluation &mdash; ${Number(score.total).toFixed(2)} (${esc(score.outcome)})</h3>`;
    if (score.summary) html += `<div class="desc">${esc(score.summary)}</div>`;
    html += '<div style="margin-top:10px">' +
      state.criteria
        .map((criterion) => {
          const entry = evidence[criterion.key] || {};
          const value = entry.score || 0;
          return `<div class="crit">
            <div class="crit-top">
              <span class="crit-score s${value}">${value || '–'}</span>
              <span>${esc(criterion.title)}</span>
              <span class="crit-conf">${esc(entry.confidence || 'unknown')}</span>
            </div>
            ${entry.evidence ? `<div class="crit-ev">${esc(entry.evidence)}</div>` : ''}
          </div>`;
        })
        .join('') +
      '</div>';

    if (data.scores.length > 1) {
      html += `<h3>Previous evaluations</h3><table class="data">
        <tr><th>Run</th><th>Score</th><th>Outcome</th><th>When</th></tr>
        ${data.scores
          .slice(1)
          .map(
            (row) => `<tr><td>#${row.run_id}</td><td>${Number(row.total).toFixed(2)}</td>
              <td>${esc(row.outcome)}</td><td>${esc(shortTime(row.scored_at))}</td></tr>`
          )
          .join('')}
      </table>`;
    }
  } else {
    html += `<h3>Evaluation</h3><p class="meta">Not evaluated yet. Use
      <strong>Run evaluation</strong> to score this property with the LLM agent.</p>`;
  }

  content.innerHTML = html;
  drawer.querySelector('.drawer-body').scrollTop = 0;
}

$('#drawer').addEventListener('click', (event) => {
  if (event.target.closest('[data-close]')) $('#drawer').hidden = true;
});
document.addEventListener('keydown', (event) => {
  if (event.key === 'Escape') {
    $('#drawer').hidden = true;
    $('#scrape-modal').hidden = true;
  }
});

/* ---------------- runs ---------------- */

async function loadRuns() {
  const [daysData, runsData] = await Promise.all([
    api(`/api/days?days=30`).catch(() => ({ days: [] })),
    api('/api/runs?limit=50').catch(() => ({ runs: [] })),
  ]);

  const today = new Date().toISOString().slice(0, 10);
  const active = daysData.days.filter((d) => d.scrapes || d.agent_runs).slice().reverse();
  $('#days').innerHTML = active.length
    ? active
        .map((day) => {
          const failed = day.failed > 0;
          const total = day.scrapes + day.agent_runs;
          return `<div class="day-card ${day.day === today ? 'today' : ''}">
            <div class="day-date">
              <span>${esc(day.day)}</span>
              <span class="pill ${failed ? 'failed' : 'done'}">${failed ? `${day.failed} failed` : 'ok'}</span>
            </div>
            <div class="day-stat"><span>Scrapes</span><span>${day.scrapes}</span></div>
            <div class="day-stat"><span>Evaluations</span><span>${day.agent_runs}</span></div>
            <div class="day-stat"><span>New properties</span><span>${day.inserted}</span></div>
            <div class="day-stat"><span>Price changes</span><span>${day.price_changes}</span></div>
          </div>`;
        })
        .join('')
    : '<div class="empty">No runs recorded yet.</div>';

  const runs = runsData.runs;
  $('#runs').innerHTML = runs.length
    ? runs.map(runHtml).join('')
    : '<div class="empty">No runs recorded yet.</div>';
}

function runHtml(run) {
  const counters = [];
  if (run.kind === 'scrape') {
    counters.push(`${run.inserted} new`, `${run.updated} updated`, `${run.price_changes} price changes`);
  } else {
    counters.push(`agent run #${run.agent_run_id ?? '—'}`, `${run.listings_seen ?? 0} seen`, `${run.listings_scored ?? 0} scored`);
  }
  const elapsed = duration(run.started_at, run.finished_at);
  const report =
    run.kind === 'agent' && run.agent_run_id
      ? `<a href="/api/runs/${run.agent_run_id}/report" target="_blank">View report &rarr;</a>`
      : '';

  return `<div class="run">
    <div class="run-top">
      <span class="run-kind ${run.kind}">${esc(run.kind)}</span>
      <span class="pill ${run.status}">${esc(run.status)}</span>
      <strong>Run #${run.run_id}</strong>
      <span class="meta">${esc(shortTime(run.started_at))}${elapsed ? ' · ' + elapsed : ''}</span>
      <span class="grow"></span>
      ${report}
    </div>
    <div class="run-counters">${counters.map(esc).join(' · ')}
      ${run.max_results ? ` · up to ${run.max_results} results / ${run.max_pages} pages${run.headless ? ' (headless)' : ''}` : ''}</div>
    ${run.search_url ? `<div class="run-url">${esc(run.search_url)}</div>` : ''}
    ${run.log ? `<details><summary>Show log</summary><pre class="run-log">${esc(run.log)}</pre></details>` : ''}
  </div>`;
}

$('#btn-refresh-runs').addEventListener('click', loadRuns);

/* ---------------- jobs ---------------- */

function setBusy(busy) {
  $('#btn-scrape').disabled = busy;
  $('#btn-evaluate').disabled = busy;
  $('#btn-cancel').hidden = !busy;
}

function renderJobLog() {
  const pane = $('#job-log');
  const stuck = pane.scrollTop + pane.clientHeight >= pane.scrollHeight - 30;
  pane.textContent = state.jobLogLines.join('\n');
  if (stuck || state.jobLogLines.length < 3) pane.scrollTop = pane.scrollHeight;
}

function attachJob(job) {
  state.activeJob = job;
  state.jobLogLines = [];
  $('#jobpanel').hidden = state.logHidden;
  $('#job-command').textContent = job.command || '';
  $('#job-log').textContent = '';
  setStatus(job.status);
  setBusy(true);
  openStream(job.job_id);
}

function setStatus(status) {
  const dot = $('#job-status');
  dot.className = `dot ${status}`;
  const titles = { running: 'Running…', done: 'Finished', failed: 'Failed' };
  $('#job-title').textContent = titles[status] || status;
}

function openStream(jobId) {
  if (state.eventSource) state.eventSource.close();
  const source = new EventSource(`/api/jobs/${jobId}/stream`);
  state.eventSource = source;

  source.addEventListener('log', (event) => {
    const payload = JSON.parse(event.data);
    state.jobLogLines.push(payload.line);
    if (state.jobLogLines.length > 2000) state.jobLogLines.shift();
    renderJobLog();
  });

  source.addEventListener('done', (event) => {
    const payload = JSON.parse(event.data);
    state.activeJob = null;
    setStatus(payload.status);
    setBusy(false);
    source.close();
    state.eventSource = null;
    toast(`${payload.kind} job ${payload.status}${payload.exit_code ? ` (exit ${payload.exit_code})` : ''}`,
      payload.status === 'failed');
    refreshAfterJob();
  });

  source.onerror = () => {
    if (state.activeJob) return; // reconnect is harmless; done will land
  };
}

function refreshAfterJob() {
  loadOverview();
  loadChart();
  loadProperties();
  const runsTab = $$('.tab').find((t) => t.dataset.tab === 'runs');
  if (runsTab && runsTab.classList.contains('is-active')) loadRuns();
}

$('#btn-scrape').addEventListener('click', () => { $('#scrape-modal').hidden = false; });
$('#scrape-modal').addEventListener('click', (event) => {
  if (event.target.closest('[data-cancel-scrape]')) $('#scrape-modal').hidden = true;
});

$('#scrape-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  const body = {
    url: $('#s-url').value.trim(),
    max_results: Number($('#s-results').value),
    max_pages: Number($('#s-pages').value),
    delay: Number($('#s-delay').value),
    headless: $('#s-headless').checked,
  };
  try {
    const result = await post('/api/scrape', body);
    $('#scrape-modal').hidden = true;
    attachJob(result.job);
    loadRuns();
  } catch (error) {
    toast(error.message, true);
  }
});

$('#btn-evaluate').addEventListener('click', async () => {
  try {
    const result = await post('/api/agent', {});
    attachJob(result.job);
    loadRuns();
  } catch (error) {
    toast(error.message, true);
  }
});

$('#btn-cancel').addEventListener('click', async () => {
  if (!state.activeJob) return;
  try {
    await post(`/api/jobs/${state.activeJob.job_id}/cancel`);
    toast('Cancelling…');
  } catch (error) {
    toast(error.message, true);
  }
});

$('#btn-minimize').addEventListener('click', () => {
  state.logHidden = !state.logHidden;
  $('#job-log').hidden = state.logHidden;
  $('#btn-minimize').textContent = state.logHidden ? 'Show log' : 'Hide log';
});

/* ---------------- boot ---------------- */

async function init() {
  try {
    const criteriaData = await api('/api/criteria');
    state.criteria = criteriaData.criteria;
  } catch (_) { /* card tooltips degrade gracefully */ }

  await Promise.all([loadOverview(), loadChart(), loadProperties()]);

  try {
    const data = await api('/api/jobs/active');
    if (data.job) attachJob(data.job);
  } catch (_) { /* server restarted */ }

  const url = new URLSearchParams().get('url');
  if (url) {
    $('#s-url').value = url;
    $('#scrape-modal').hidden = false;
  }
}

init();
