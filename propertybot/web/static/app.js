'use strict';

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => Array.from(document.querySelectorAll(sel));

const state = {
  limit: 24,
  offset: 0,
  total: 0,
  criteria: [],
  outcome: '',
  eventSource: null,
  activeJob: null,
  jobLogLines: [],
  logHidden: false,
  chartMode: 'scrapes',
  chartDays: 30,
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

const artNo = (id) =>
  String(id).padStart(8, '0').replace(/(\d{3})(\d{3})(\d{2}).*/, '$1.$2.$3');

function activateOnKey(node, fn) {
  node.addEventListener('keydown', (event) => {
    if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault();
      fn();
    }
  });
}

let toastTimer = null;
function toast(message, isError = false) {
  const node = $('#toast');
  node.textContent = message;
  node.classList.toggle('error', isError);
  node.setAttribute('role', isError ? 'alert' : 'status');
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

/* ---------------- overlays: focus trap + restore ---------------- */

let lastFocus = null;

function focusablesIn(container) {
  return Array.from(
    container.querySelectorAll(
      'button, [href], input, select, textarea, summary, [tabindex]:not([tabindex="-1"])'
    )
  ).filter((el) => !el.disabled && el.getClientRects().length > 0);
}

function openOverlay(overlay) {
  lastFocus = document.activeElement;
  overlay.hidden = false;
  const focusables = focusablesIn(overlay);
  (focusables[0] || overlay.querySelector('.drawer-body')).focus();
}

function closeOverlay(overlay) {
  overlay.hidden = true;
  if (lastFocus && document.contains(lastFocus)) lastFocus.focus();
  lastFocus = null;
}

document.addEventListener('keydown', (event) => {
  if (event.key === 'Escape') {
    if (!$('#drawer').hidden) closeOverlay($('#drawer'));
    if (!$('#scrape-modal').hidden) closeOverlay($('#scrape-modal'));
    return;
  }
  if (event.key === 'Tab') {
    const open = [$('#drawer'), $('#scrape-modal')].find((el) => !el.hidden);
    if (!open) return;
    const focusables = focusablesIn(open);
    if (!focusables.length) return;
    const first = focusables[0];
    const last = focusables[focusables.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
    return;
  }
  if (event.key === '/' && !/^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement.tagName)) {
    event.preventDefault();
    $('#f-q').focus();
  }
});

/* ---------------- tabs ---------------- */

$$('.nav-link').forEach((tab) => {
  tab.addEventListener('click', () => {
    $$('.nav-link').forEach((other) => {
      const active = other === tab;
      other.classList.toggle('is-active', active);
      other.setAttribute('aria-selected', String(active));
    });
    $$('.tab-panel').forEach((panel) => {
      panel.classList.toggle('is-active', panel.id === `tab-${tab.dataset.tab}`);
    });
    if (tab.dataset.tab === 'runs') loadRuns();
  });
});

function showTab(name) {
  const tab = $$('.nav-link').find((t) => t.dataset.tab === name);
  if (tab) tab.click();
}

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
      (t) => `<div class="tick-item">
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

  loadOutcomeSpread();
}

async function loadOutcomeSpread() {
  let facets;
  try {
    facets = (await api('/api/properties?limit=1')).facets;
  } catch (_) {
    return;
  }
  const counts = (facets && facets.outcomes) || {};
  const order = ['GREAT', 'GOOD', 'OK', 'FAIL', 'unscored'];
  const total = order.reduce((sum, key) => sum + (counts[key] || 0), 0);
  const box = $('#outcome-spread');
  if (!total) {
    box.innerHTML = '<p class="meta">Nothing scored yet — run an evaluation to see the spread.</p>';
    return;
  }
  const bar = order
    .filter((key) => counts[key])
    .map(
      (key) =>
        `<span class="seg-fill sp-${key}" style="width:${((counts[key] / total) * 100).toFixed(1)}%" title="${key}: ${counts[key]}"></span>`
    )
    .join('');
  const legend = order
    .filter((key) => counts[key])
    .map(
      (key) =>
        `<span><i class="dot" style="background:${key === 'unscored' ? 'var(--surface-3)' : `var(--${key.toLowerCase()})`}"></i>${key === 'unscored' ? 'Unscored' : key} <span class="count">${counts[key]}</span></span>`
    )
    .join('');
  box.innerHTML = `<div class="spread-bar" role="img" aria-label="Outcome spread: ${legend ? order.filter((k) => counts[k]).map((k) => `${k} ${counts[k]}`).join(', ') : ''}">${bar}</div>
    <div class="spread-legend">${legend}</div>`;
}

async function loadTopPicks() {
  const box = $('#top-picks');
  let data;
  try {
    data = await api('/api/properties?sort=score_desc&limit=5');
  } catch (error) {
    box.innerHTML = `<div class="empty">Could not load top picks: ${esc(error.message)}</div>`;
    return;
  }
  const scored = data.properties.filter((p) => p.score !== null && p.score !== undefined);
  if (!scored.length) {
    box.innerHTML = `<div class="empty"><strong>No scored listings yet</strong>
      Run an evaluation and the best co-living candidates land here.</div>`;
    return;
  }
  box.innerHTML = scored
    .map((p, index) => {
      const thumb = p.image_url
        ? `<span class="pick-thumb" style="background-image:url('${esc(p.image_url)}')" aria-hidden="true"></span>`
        : '<span class="pick-thumb" aria-hidden="true">&#127968;</span>';
      return `<div class="pick-row" role="button" tabindex="0" data-id="${p.listing_id}"
          aria-label="${esc(p.title || 'Untitled')}, score ${Number(p.score).toFixed(2)}, ${esc(p.outcome || '')}">
        <span class="pick-rank">${index + 1}</span>
        ${thumb}
        <span class="pick-main">
          <span class="pick-title">${esc(p.title || 'Untitled')}</span>
          <span class="pick-addr">${esc(p.district || p.address || '')}</span>
        </span>
        <span class="pick-score">
          <span class="score-num ${esc(p.outcome || 'none')}" style="font-size:17px">${Number(p.score).toFixed(2)}</span>
          <span class="pill ${esc(p.outcome || 'none')}">${esc(p.outcome || '')}</span>
        </span>
        <span class="price-tag">${esc(money(p.price_value) || p.price || 'n/a')}</span>
      </div>`;
    })
    .join('');
  $$('#top-picks .pick-row').forEach((row) => {
    const open = () => openProperty(Number(row.dataset.id));
    row.addEventListener('click', open);
    activateOnKey(row, open);
  });
}

async function loadChart() {
  let data;
  try {
    data = await api(`/api/days?days=${state.chartDays}`);
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
      if (state.chartMode === 'scrapes') {
        build(row.failed, 'fail');
        build(row.scrapes - row.failed, 'scrape');
      } else {
        build(row.agent_runs, 'eval');
      }
      const date = new Date(row.day + 'T00:00:00');
      const label = showTicks || index % Math.ceil(rows.length / 8) === 0
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

$('#chart-range').addEventListener('click', (event) => {
  const chip = event.target.closest('.chip');
  if (!chip) return;
  state.chartDays = Number(chip.dataset.days);
  $$('#chart-range .chip').forEach((other) => {
    const active = other === chip;
    other.classList.toggle('is-active', active);
    other.setAttribute('aria-pressed', String(active));
  });
  loadChart();
});

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
  min_price: () => $('#f-min').value,
  max_price: () => $('#f-max').value,
  sort: () => $('#f-sort').value,
};

const OUTCOME_CHIPS = [
  ['', 'All'],
  ['GREAT', 'GREAT'],
  ['GOOD', 'GOOD'],
  ['OK', 'OK'],
  ['FAIL', 'FAIL'],
  ['unscored', 'Unscored'],
];

function renderOutcomeChips(counts = {}) {
  const rail = $('#outcome-chips');
  const totalCount = Object.values(counts).reduce((a, b) => a + b, 0);
  rail.innerHTML = OUTCOME_CHIPS.map(([value, label]) => {
    const count = value === '' ? totalCount : counts[value] || 0;
    const active = state.outcome === value;
    return `<button class="chip ${active ? 'is-active' : ''}" data-outcome="${value}" aria-pressed="${active}">
      ${label}<span class="chip-count">${count}</span>
    </button>`;
  }).join('');
}

$('#outcome-chips').addEventListener('click', (event) => {
  const chip = event.target.closest('.chip');
  if (!chip) return;
  state.outcome = chip.dataset.outcome;
  state.offset = 0;
  loadProperties();
});

let filterTimer = null;
$('#f-q').addEventListener('input', () => {
  clearTimeout(filterTimer);
  filterTimer = setTimeout(() => {
    state.offset = 0;
    if ($('#f-q').value.trim()) showTab('properties');
    loadProperties();
  }, 350);
});
['#f-min', '#f-max'].forEach((sel) => {
  $(sel).addEventListener('input', () => {
    clearTimeout(filterTimer);
    filterTimer = setTimeout(() => { state.offset = 0; loadProperties(); }, 350);
  });
});
['#f-district', '#f-type', '#f-sort'].forEach((sel) => {
  $(sel).addEventListener('change', () => { state.offset = 0; loadProperties(); });
});
$('#btn-reset').addEventListener('click', () => {
  ['#f-q', '#f-district', '#f-type', '#f-min', '#f-max', '#f-sort'].forEach((sel) => {
    $(sel).value = sel === '#f-sort' ? 'newest' : '';
  });
  state.outcome = '';
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

function critBarsHtml(p) {
  const keys = state.criteria.length
    ? state.criteria.map((c) => ({ key: c.key, index: c.index, title: c.title, description: c.description }))
    : [1, 2, 3, 4, 5, 6, 7, 8].map((i) => ({ key: `c${i}`, index: i, title: `Criterion ${i}`, description: '' }));
  return `<div class="card-crits">${keys
    .map((criterion) => {
      const value = p[criterion.key] || 0;
      const segs = [1, 2, 3, 4]
        .map((i) => `<i class="seg ${i <= value ? `on${value}` : ''}"></i>`)
        .join('');
      return `<div class="crit-mini" title="${esc(criterion.title)}${criterion.description ? ': ' + esc(criterion.description) : ''}">
        <span class="crit-label">${esc(criterion.title)}</span>
        <span class="segbar4" aria-label="${esc(criterion.title)}: ${value || 'no'} of 4">${segs}</span>
      </div>`;
    })
    .join('')}</div>`;
}

function cardHtml(p) {
  const thumb = p.image_url
    ? `<span class="card-thumb" style="background-image:url('${esc(p.image_url)}')" aria-hidden="true"></span>`
    : '<span class="card-thumb" aria-hidden="true">&#127968;</span>';

  const scored = p.score !== null && p.score !== undefined;
  const scoreBlock = scored
    ? `<div class="card-score-row">
         <span class="score-num ${esc(p.outcome || 'none')}">${Number(p.score).toFixed(2)}</span>
         <span class="score-of">/ 4</span>
         <span class="pill ${esc(p.outcome || 'none')}">${esc(p.outcome || '')}</span>
       </div>`
    : `<div class="card-score-row">
         <span class="score-num none">—</span>
         <span class="pill none">Not evaluated</span>
       </div>`;

  const delta = p.first_price && p.price_value && p.first_price !== p.price_value
    ? `<span class="delta ${p.price_value > p.first_price ? 'up' : 'down'}"
         title="vs first seen price">${p.price_value > p.first_price ? '▲' : '▼'} ${Math.abs(((p.price_value - p.first_price) / p.first_price) * 100).toFixed(1)}%</span>`
    : '';

  return `<article class="card" data-id="${p.listing_id}" role="button" tabindex="0"
      aria-label="${esc(p.title || 'Untitled')}${scored ? `, score ${Number(p.score).toFixed(2)} of 4, ${esc(p.outcome)}` : ', not evaluated'}">
    <div class="card-top">
      ${thumb}
      <div class="card-head">
        <div class="card-title">${esc(p.title || 'Untitled')}</div>
        <div class="card-addr">${esc(p.address || p.district || 'Address n/a')}</div>
      </div>
    </div>
    ${scoreBlock}
    ${scored ? critBarsHtml(p) : ''}
    <div class="card-foot">
      <span class="price-tag">${esc(money(p.price_value) || p.price || 'Price n/a')}</span>
      ${p.price_per_area ? `<span class="card-psf">${esc(p.price_per_area)}</span>` : ''}
      ${delta}
      <span class="art-no">${artNo(p.listing_id)}</span>
    </div>
  </article>`;
}

async function loadProperties() {
  const query = new URLSearchParams({ limit: state.limit, offset: state.offset });
  Object.entries(filters).forEach(([key, getter]) => {
    const value = getter();
    if (value !== '') query.set(key, value);
  });
  if (state.outcome) query.set('outcome', state.outcome);

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
    : '<div class="empty"><strong>No properties match</strong>Loosen the filters or run a fresh scrape.</div>';

  $$('#props .card').forEach((card) => {
    const open = () => openProperty(Number(card.dataset.id));
    card.addEventListener('click', open);
    activateOnKey(card, open);
  });

  syncFacets(data.facets);
  renderOutcomeChips((data.facets && data.facets.outcomes) || {});
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

/* ---------------- property detail ---------------- */

function priceChartSvg(history) {
  const pts = (history || []).filter((row) => row.price_value != null);
  if (pts.length < 2) return null;
  const w = 600, h = 130, pad = 10;
  const values = pts.map((p) => p.price_value);
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = max - min || 1;
  const stepX = (w - pad * 2) / (pts.length - 1);
  const coords = pts.map(
    (p, i) =>
      `${(pad + i * stepX).toFixed(1)},${(h - pad - ((p.price_value - min) / span) * (h - pad * 2)).toFixed(1)}`
  );
  const [lastX, lastY] = coords[coords.length - 1].split(',');
  return `<svg viewBox="0 0 ${w} ${h}" role="img" aria-label="Asking price over ${pts.length} observations, ${money(min)} to ${money(max)}">
    <polyline points="${coords.join(' ')}" fill="none" stroke="var(--blue-hi)" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>
    <circle cx="${lastX}" cy="${lastY}" r="4.5" fill="var(--yellow)"/>
  </svg>`;
}

async function openProperty(listingId) {
  const drawer = $('#drawer');
  const content = $('#drawer-content');
  content.innerHTML = '<p class="meta">Loading…</p>';
  openOverlay(drawer);

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

  let main = hero;
  if (images.length > 1) {
    main += `<div class="gallery">${images
      .map((src) => `<img src="${esc(src)}" alt="" loading="lazy">`)
      .join('')}</div>`;
  }

  main += `<h2 id="drawer-title">${esc(l.title || 'Untitled')}</h2>
    <div class="addr-lg">${esc(l.address || 'Address n/a')}</div>
    <div class="price-lg">${esc(money(l.price_value) || l.price || 'Price n/a')}
      ${l.price_per_area ? `<span class="card-psf">${esc(l.price_per_area)}</span>` : ''}</div>`;

  if (data.price_history.length) {
    const chart = priceChartSvg(data.price_history);
    main += '<h3>Price history</h3>';
    if (chart) {
      main += `<div class="price-chart">${chart}</div>`;
    } else {
      main += '<p class="chart-note">One observation so far — the trend line appears after a price change is scraped.</p>';
    }
    if (data.price_history.length > 1) {
      main += `<table class="data">
        <tr><th>Seen</th><th>Price</th></tr>
        ${data.price_history
          .map(
            (row) => `<tr><td>${esc(shortTime(row.seen_at))}</td><td>${esc(money(row.price_value))}</td></tr>`
          )
          .join('')}
      </table>`;
    }
  }

  main += '<h3>Details</h3><dl class="kv">' +
    rows
      .filter(([, value]) => value !== null && value !== undefined && value !== '')
      .map(([key, value]) => `<dt>${esc(key)}</dt><dd>${esc(value)}</dd>`)
      .join('') +
    '</dl>';

  if (l.description) {
    main += `<h3>Description</h3><div class="desc">${esc(l.description)}</div>`;
  }

  let ticket = '';
  if (score) {
    const evidence = (score.evidence && score.evidence.criteria) || {};
    ticket += `<div class="ticket-score">
        <span class="score-num ${esc(score.outcome || 'none')}">${Number(score.total).toFixed(2)}</span>
        <span class="score-of">/ 4</span>
        <span class="pill ${esc(score.outcome || 'none')}">${esc(score.outcome || '')}</span>
      </div>`;
    if (score.summary) ticket += `<p class="ticket-summary">${esc(score.summary)}</p>`;
    ticket += '<h3>Criteria</h3>' +
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
        .join('');

    if (data.scores.length > 1) {
      ticket += `<h3>Previous evaluations</h3><table class="data">
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
    ticket += `<div class="ticket-score"><span class="score-num none">—</span>
      <span class="pill none">Not evaluated</span></div>
      <p class="ticket-summary">Run an evaluation to score this property with the LLM agent.</p>`;
  }
  ticket += `<a class="btn btn-blue btn-block" href="${esc(l.url)}" target="_blank" rel="noopener">Open on PropertyGuru</a>`;

  content.innerHTML = `<div class="drawer-grid"><div class="drawer-main">${main}</div><aside class="ticket">${ticket}</aside></div>`;
  drawer.querySelector('.drawer-body').scrollTop = 0;
}

$('#drawer').addEventListener('click', (event) => {
  if (event.target.closest('[data-close]')) closeOverlay($('#drawer'));
});

/* ---------------- runs ---------------- */

async function loadRuns() {
  const [daysData, runsData] = await Promise.all([
    api(`/api/days?days=30`).catch(() => ({ days: [] })),
    api('/api/runs?limit=50').catch(() => ({ runs: [] })),
  ]);

  const today = new Date().toISOString().slice(0, 10);
  $('#days').innerHTML = daysData.days.length
    ? daysData.days
        .map((day) => {
          const total = day.scrapes + day.agent_runs;
          const level = day.failed > 0 ? 'failed' : total === 0 ? '' : `h${Math.min(4, total)}`;
          return `<span class="heat ${level} ${day.day === today ? 'today' : ''}"
            title="${day.day}: ${day.scrapes} scrape(s), ${day.agent_runs} evaluation(s)${day.failed ? `, ${day.failed} failed` : ''}"></span>`;
        })
        .join('')
    : '<div class="empty"><strong>No runs recorded yet</strong>Start a scrape and the last 30 days fill in here.</div>';

  const runs = runsData.runs;
  $('#runs').innerHTML = runs.length
    ? runs.map(runHtml).join('')
    : '<div class="empty"><strong>No runs recorded yet</strong>Start a scrape to create the first run.</div>';
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

  return `<div class="run ${run.kind === 'agent' ? 'run-agent' : ''}">
    <div class="run-top">
      <span class="run-kind ${run.kind}">${esc(run.kind)}</span>
      <span class="pill ${esc(run.status)}">${esc(run.status)}</span>
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
  loadTopPicks();
  const runsTab = $$('.nav-link').find((t) => t.dataset.tab === 'runs');
  if (runsTab && runsTab.classList.contains('is-active')) loadRuns();
}

$('#btn-scrape').addEventListener('click', () => openOverlay($('#scrape-modal')));
$('#scrape-modal').addEventListener('click', (event) => {
  if (event.target.closest('[data-cancel-scrape]')) closeOverlay($('#scrape-modal'));
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
    closeOverlay($('#scrape-modal'));
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

  renderOutcomeChips();
  await Promise.all([loadOverview(), loadChart(), loadProperties(), loadTopPicks()]);

  try {
    const data = await api('/api/jobs/active');
    if (data.job) attachJob(data.job);
  } catch (_) { /* server restarted */ }

  const url = new URLSearchParams(location.search).get('url');
  if (url) {
    $('#s-url').value = url;
    openOverlay($('#scrape-modal'));
  }
}

init();
