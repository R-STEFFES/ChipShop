// ChipShop -- Frontend
// Bindet templates/index.html an die Flask-API:
//   GET  /api/catalog                 -> Presets, Kerntypen, Module, GPUs, Sim-Partner
//   GET  /api/workloads               -> fertige RISC-V-Programme
//   POST /api/estimate                -> Fläche, Leistung, Wafer, Simulation
//   POST /api/config[?write=1]        -> Scala-Config anzeigen/ablegen
//   POST /api/build                   -> Verilog-Build starten
//   POST /api/simulate                -> C++-Simulator bauen/starten
//   GET  /api/build/<id>              -> Status + Log (für beide Job-Arten)
//   GET  /api/build/<id>/files|zip    -> Artefakte
//   GET  /api/jobs                    -> frühere Läufe

const API = '/api';

const state = {
  catalog: null,
  spec: null,
  estimate: null,
  jobId: null,
  jobKind: null,
  jobTimer: null,
  version: 0,          // entwertet veraltete Antworten
  activePreset: null,
  gpuChoice: null,     // Auswahl im GPU-Fenster
  workloads: [],
};

const els = {};
const $ = (id) => document.getElementById(id);

// ------------------------------------------------------------------ Helfer

function num(x, d = 2) {
  if (x === null || x === undefined || Number.isNaN(x)) return '–';
  return Number(x).toLocaleString('de-DE', {
    minimumFractionDigits: d, maximumFractionDigits: d,
  });
}

function bytes(n) {
  if (!n && n !== 0) return '–';
  const u = ['B', 'KiB', 'MiB', 'GiB'];
  let i = 0;
  while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
  return num(n, n < 10 && i > 0 ? 1 : 0) + ' ' + u[i];
}

function duration(hours) {
  if (!isFinite(hours)) return '–';
  const s = hours * 3600;
  if (s < 90) return num(s, 0) + ' s';
  if (s < 5400) return num(s / 60, 1) + ' min';
  if (hours < 48) return num(hours, 2) + ' h';
  return num(hours / 24, 1) + ' Tage';
}

function secs(s) {
  if (!isFinite(s) || s < 0) return '–';
  if (s < 60) return num(s, 0) + ' s';
  const m = Math.floor(s / 60);
  return m + ' min ' + num(s % 60, 0) + ' s';
}

function esc(t) {
  return String(t ?? '').replace(/[&<>"']/g, (c) => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]
  ));
}

function toast(msg, type = 'info', ms = 4200) {
  document.querySelectorAll('.toast').forEach((t) => t.remove());
  const el = document.createElement('div');
  el.className = 'toast ' + type;
  el.textContent = msg;
  document.body.appendChild(el);
  setTimeout(() => el.remove(), ms);
}

async function api(path, opts) {
  const res = await fetch(API + path, opts);
  let body = null;
  try { body = await res.json(); } catch (_) { /* HTML-Fehlerseite */ }
  if (!res.ok) {
    const msg = (body && (body.error || body.message)) ||
      (res.status + ' ' + res.statusText);
    const err = new Error(msg);
    err.body = body;
    throw err;
  }
  return body;
}

const post = (path, data) => api(path, {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(data),
});

function clampInt(v, lo, hi, dflt) {
  const n = parseInt(v, 10);
  if (Number.isNaN(n)) return dflt;
  return Math.min(hi, Math.max(lo, n));
}

// -------------------------------------------------------------- Katalog

async function loadCatalog() {
  const cat = await api('/catalog');
  state.catalog = cat;
  state.spec = {
    ...cat.defaults,
    modules: [...cat.defaults.modules],
    sim_partners: [...(cat.defaults.sim_partners || [])],
    core_mix: { [cat.defaults.core_type]: cat.defaults.cores },
  };

  renderChipyardInfo(cat);
  renderPresets();
  renderCoreMix();
  renderPdks();
  renderL3();
  renderNpus();
  renderModules();
  renderSimPartners();
  renderDramOptions();
  fillFormFromSpec();
  renderGpuSummary();

  els['target-path'].textContent = cat.config_path || '–';
}

function renderChipyardInfo(cat) {
  const cy = cat.chipyard || {};
  const box = els['chipyard-info'];
  const okDot = cy.exists && cy.conda ? 'ok' : 'bad';
  const simPill = cy.simulator_built
    ? `<span class="pill ok"><span class="dot ok"></span>C++-Simulator gebaut</span>`
    : `<span class="pill"><span class="dot"></span>C++-Simulator noch nicht gebaut</span>`;
  box.innerHTML =
    `<span class="pill ${okDot}"><span class="dot ${okDot}"></span>` +
    `Chipyard <b>${esc(cy.version || '?')}</b></span>` +
    `<span class="pill">Config <b>${esc(cat.config_class || '–')}</b></span>` +
    `<span class="pill ${cy.root ? (cy.exists ? 'ok' : 'bad') : 'bad'}">` +
    `${esc(cy.root || 'Pfad unbekannt')}</span>` +
    (cy.conda ? '' : '<span class="pill bad">Conda-Umgebung fehlt</span>') +
    simPill;
  if (!cy.exists || !cy.conda) {
    toast('Chipyard-Checkout oder Conda-Umgebung fehlt – ' +
          'Bauen und Simulieren sind gesperrt, Schätzung geht trotzdem.', 'warning', 9000);
  }
}

const PRESETS = [
  { id: 'fento', label: 'STI-Fento',
    note: 'Minimaler Linux-Chip in grober Fertigung (130 nm)',
    spec: {
      cores: 1, core_type: 'rocket', l3_kib: 256, l3_ways: 8, l3_banks: 1,
      pdk: 'sky130', dram_mib: 256, linux_target: true,
      npu_enabled: false, gpu_enabled: false,
      modules: ['uart', 'gpio', 'dmi', 'blockdev', 'spiflash'] } },
  { id: 'nano', label: 'STI-Nano',
    note: 'Zwei Kerne, 45 nm, Linux mit etwas Luft',
    spec: {
      cores: 2, core_type: 'rocket', l3_kib: 512, l3_ways: 8, l3_banks: 1,
      pdk: 'nangate45', dram_mib: 512, linux_target: true,
      npu_enabled: false, gpu_enabled: false,
      modules: ['uart', 'gpio', 'spi', 'i2c', 'dmi', 'blockdev', 'spiflash'] } },
  { id: 'mikro', label: 'STI-Mikro',
    note: 'Vier Kerne in 28 nm, L3 mit zwei Banks, kleine NPU',
    spec: {
      cores: 4, core_type: 'rocket', l3_kib: 1024, l3_ways: 8, l3_banks: 2,
      pdk: 'tsmc28', dram_mib: 1024, linux_target: true,
      npu_enabled: true, npu_id: 'gemmini_16', gpu_enabled: false,
      modules: ['uart', 'gpio', 'spi', 'i2c', 'dmi', 'blockdev', 'spiflash'] } },
  { id: 'mill', label: 'STI-Mill',
    note: 'Vier OoO-Kerne in 28 nm mit großer NPU und GPU',
    spec: {
      cores: 4, core_type: 'boom_medium', l3_kib: 2048, l3_ways: 8,
      l3_banks: 2, pdk: 'tsmc28', dram_mib: 2048, linux_target: true,
      npu_enabled: true, npu_id: 'gemmini_32', gpu_enabled: true,
      gpu_id: 'powervr_gt',
      modules: ['uart', 'gpio', 'spi', 'i2c', 'dmi', 'blockdev', 'spiflash'] } },
  { id: 'meter', label: 'STI-Meter',
    note: 'Acht OoO-Kerne in 7 nm – Spitzenklasse',
    spec: {
      cores: 8, core_type: 'vexii', l3_kib: 4096, l3_ways: 16, l3_banks: 4,
      pdk: 'generic7', dram_mib: 8192, linux_target: true,
      npu_enabled: true, npu_id: 'gemmini_32', gpu_enabled: true,
      gpu_id: 'mali_g710',
      modules: ['uart', 'gpio', 'spi', 'i2c', 'dmi', 'blockdev', 'spiflash'] } },
  { id: 'pico', label: 'Mikrocontroller',
    note: 'Ibex-Kern (RV32) wie im Raspberry Pi Pico – kein Linux',
    spec: {
      cores: 1, core_type: 'ibex', l3_kib: 128, l3_ways: 4, l3_banks: 1,
      pdk: 'sky130', dram_mib: 64, linux_target: false,
      npu_enabled: false, gpu_enabled: false,
      modules: ['uart', 'gpio', 'dmi'] } },
  { id: 'many', label: '16-Kern Many-Core',
    note: 'Viele Rocket-Kerne für Durchsatz',
    spec: {
      cores: 16, core_type: 'rocket', l3_kib: 4096, l3_ways: 8, l3_banks: 4,
      pdk: 'sky130', dram_mib: 2048, linux_target: true,
      npu_enabled: false, gpu_enabled: false,
      modules: ['uart', 'gpio', 'dmi', 'blockdev', 'spiflash'] } },
];

function renderPresets() {
  const box = els['preset-row'];
  box.innerHTML = '';
  PRESETS.forEach((p) => {
    const b = document.createElement('button');
    b.type = 'button';
    b.className = 'preset';
    b.dataset.preset = p.id;
    b.textContent = p.label;
    b.title = noteFor(p);
    b.addEventListener('click', () => applyPreset(p));
    box.appendChild(b);
  });
}

function noteFor(p) {
  return (p.note ? p.note + ' – ' : '') + 'Vorlage laden: ' + p.label;
}


function applyPreset(p) {
  const coreMix = p.spec.core_mix || { [p.spec.core_type]: p.spec.cores };
  state.spec = {
    ...state.spec,
    ...p.spec,
    core_mix: { ...coreMix },
    cores: Object.values(coreMix).reduce((sum, count) => sum + count, 0),
    modules: [...p.spec.modules],
  };
  state.spec.core_type = primaryCore(coreMix);
  if (p.spec.gpu_enabled === false) {
    state.spec.gpu_enabled = false;
    state.spec.gpu_id = null;
  }
  state.activePreset = p.id;
  document.querySelectorAll('.preset').forEach((b) => {
    b.classList.toggle('active', b.dataset.preset === p.id);
  });
  fillFormFromSpec();
  renderGpuSummary();
  scheduleEstimate();
}

// -------------------------------------------------------------- Kerne

function coreTotal(mix = state.spec.core_mix) {
  return Object.values(mix || {}).reduce((sum, count) => sum + Number(count || 0), 0);
}

function primaryCore(mix = state.spec.core_mix) {
  return Object.keys(mix || {}).find((id) => Number(mix[id]) > 0) || 'rocket';
}

function renderCoreMix() {
  const box = els['core-list'];
  if (!box || !state.catalog) return;
  const entries = Object.entries(state.spec.core_mix || {})
    .filter(([, count]) => Number(count) > 0);
  if (!entries.length) entries.push(['rocket', 1]);
  state.spec.core_mix = Object.fromEntries(entries);
  box.innerHTML = '';

  entries.forEach(([selected, count], index) => {
    const row = document.createElement('div');
    row.className = 'core-row';
    const select = document.createElement('select');
    select.setAttribute('aria-label', 'Kerntyp ' + (index + 1));
    const occupied = new Set(entries.map(([id], i) => i === index ? null : id));
    state.catalog.core_types.forEach((core) => {
      const option = document.createElement('option');
      option.value = core.id;
      option.textContent = core.label;
      option.title = core.uarch || '';
      option.disabled = occupied.has(core.id);
      select.appendChild(option);
    });
    select.value = selected;

    const amount = document.createElement('input');
    amount.type = 'number';
    amount.min = '1';
    amount.max = '64';
    amount.step = '1';
    amount.value = count;
    amount.setAttribute('aria-label', 'Anzahl ' + selected);

    const remove = document.createElement('button');
    remove.type = 'button';
    remove.className = 'btn remove-core';
    remove.textContent = '×';
    remove.title = selected + ' entfernen';
    remove.disabled = entries.length === 1;
    remove.setAttribute('aria-label', selected + ' entfernen');

    select.addEventListener('change', () => {
      const next = Object.fromEntries(entries);
      const currentCount = Number(next[selected]);
      delete next[selected];
      next[select.value] = currentCount;
      state.spec.core_mix = next;
      coreMixChanged();
    });
    amount.addEventListener('change', () => {
      const next = { ...state.spec.core_mix };
      const otherTotal = coreTotal(next) - Number(next[selected]);
      next[selected] = clampInt(amount.value, 1, 64 - otherTotal, 1);
      state.spec.core_mix = next;
      coreMixChanged();
    });
    remove.addEventListener('click', () => {
      const next = { ...state.spec.core_mix };
      delete next[selected];
      state.spec.core_mix = next;
      coreMixChanged();
    });

    row.append(select, amount, remove);
    box.appendChild(row);
  });
  const total = coreTotal();
  els['core-total'].textContent = `${total} / 64 Kerne`;
  els['btn-add-core'].disabled = total >= 64 ||
    entries.length >= state.catalog.core_types.length;
}

function coreMixChanged() {
  state.spec.cores = coreTotal();
  state.spec.core_type = primaryCore();
  state.activePreset = null;
  document.querySelectorAll('.preset').forEach((button) => button.classList.remove('active'));
  renderCoreMix();
  hideConfig();
  updateHints();
  scheduleEstimate();
}

function addCoreType() {
  const used = new Set(Object.keys(state.spec.core_mix));
  const next = state.catalog.core_types.find((core) => !used.has(core.id));
  if (!next || coreTotal() >= 64) return;
  state.spec.core_mix[next.id] = 1;
  coreMixChanged();
}

// --------------------------------------------------------- PDK / L3

function renderPdks() {
  const sel = els['pdk'];
  sel.innerHTML = '';
  state.catalog.presets.forEach((p) => {
    const o = document.createElement('option');
    o.value = p.id;
    o.textContent = p.label;
    o.title = p.note || '';
    sel.appendChild(o);
  });
}

function renderL3() {
  const size = els['l3'];
  size.innerHTML = '';
  (state.catalog.l3_choices || []).forEach((k) => {
    const o = document.createElement('option');
    o.value = String(k);
    o.textContent = k >= 1024 ? (k / 1024) + ' MiB' : k + ' KiB';
    size.appendChild(o);
  });
  const ways = els['l3-ways'];
  ways.innerHTML = '';
  (state.catalog.l3_ways_choices || []).forEach((k) => {
    const o = document.createElement('option');
    o.value = String(k);
    o.textContent = k + '-fach';
    ways.appendChild(o);
  });
  const banks = els['l3-banks'];
  banks.innerHTML = '';
  (state.catalog.l3_bank_choices || []).forEach((k) => {
    const o = document.createElement('option');
    o.value = String(k);
    o.textContent = String(k);
    banks.appendChild(o);
  });
}

function syncL3Controls() {
  const on = els['l3-enabled'].checked;
  ['l3', 'l3-ways', 'l3-banks'].forEach((id) => { els[id].disabled = !on; });
  els['l3-state'].textContent = on ? 'an' : 'aus';
  els['l3-hint'].textContent = on
    ? 'Gemeinsamer Cache aller Kerne. Mehr Größe hilft bei großen Programmen, kostet Fläche.'
    : 'Ohne L3: nur ein Kohärenz-Hub. Spart Fläche, aber jeder Zugriff geht zum DRAM.';
}

// -------------------------------------------------------------- GPU

function currentNodeNm() {
  const pdk = state.catalog.presets.find((p) => p.id === els['pdk'].value);
  return pdk ? Number(pdk.node_nm) : 28;
}

function areaAtNode(area28) {
  const cat = state.catalog || {};
  const ref = Number(cat.area_ref_nm) || 28;
  const exp = Number(cat.area_scale_exp) || 1.3;
  return Number(area28) * Math.pow(currentNodeNm() / ref, exp);
}

function renderGpuSummary() {
  const box = els['gpu-summary'];
  const gpu = state.spec.gpu_enabled && state.spec.gpu_id
    ? (state.catalog.gpus || []).find((g) => g.id === state.spec.gpu_id)
    : null;
  els['gpu-tag'].textContent = gpu ? gpu.kind : 'keine';
  if (!gpu) {
    box.innerHTML = '<span class="muted">Keine GPU ausgewählt.</span>';
    return;
  }
  const area = areaAtNode(gpu.area28);
  const vram = gpu.shared_memory
    ? 'teilt den DRAM'
    : `${num(gpu.vram_gib, 0)} GiB eigener Speicher`;
  box.innerHTML =
    `<div class="gpu-line"><b>${esc(gpu.label)}</b>` +
    `<span class="tag est">nur Schätzung</span></div>` +
    `<div class="gpu-specs">${esc(gpu.vendor)} · ${esc(gpu.kind)} · ` +
    `${num(area, 1)} mm² · ${vram}</div>`;
}

function openGpuDialog() {
  const grid = els['gpu-grid'];
  grid.innerHTML = '';
  state.gpuChoice = state.spec.gpu_enabled ? state.spec.gpu_id : null;

  const entries = [{
    id: '', label: 'Keine GPU', vendor: '–', kind: 'keine', area28: 0,
    note: 'Reiner CPU-Chip ohne Grafik.', available: true,
  }, ...state.catalog.gpus];

  entries.forEach((g) => {
    const isNone = !g.id;
    const area = isNone ? 0 : areaAtNode(g.area28);
    const label = document.createElement('label');
    label.className = 'gpu-card' + (isNone ? ' none' : '');
    label.dataset.gpu = g.id || '';
    const checked = (state.gpuChoice || '') === (g.id || '');
    if (checked) label.classList.add('selected');

    const vram = isNone ? '–'
      : g.shared_memory ? 'teilt DRAM' : `${num(g.vram_gib, 0)} GiB`;
    label.innerHTML =
      `<input type="radio" name="gpu-choice" value="${esc(g.id || '')}"` +
      `${checked ? ' checked' : ''}>` +
      `<div class="gpu-card-body">` +
      `<div class="gpu-card-title">${esc(g.label)}</div>` +
      `<div class="gpu-card-meta">${esc(g.vendor)} · ${esc(g.kind)}</div>` +
      `<div class="gpu-card-stats">` +
      `<span>${isNone ? '–' : num(area, 1) + ' mm²'}</span>` +
      `<span>${vram}</span>` +
      `<span>${isNone ? '–' : num(g.flops_per_cycle * g.fmax_ghz / 1000, 1) + ' TFLOP/s'}</span>` +
      `</div>` +
      (g.api ? `<div class="gpu-card-api">${esc(g.api)}</div>` : '') +
      (g.note ? `<div class="gpu-card-note">${esc(g.note)}</div>` : '') +
      (isNone || g.fragment ? '' :
        '<div class="gpu-card-warn">kein Chipyard-Fragment – nur Schätzung</div>') +
      `</div>`;
    label.addEventListener('click', () => {
      state.gpuChoice = g.id || null;
      grid.querySelectorAll('.gpu-card').forEach((c) => c.classList.remove('selected'));
      label.classList.add('selected');
    });
    grid.appendChild(label);
  });

  els['gpu-dialog-hint'].textContent =
    `${state.catalog.gpus.length} GPUs zur Auswahl · Fläche für ` +
    `${currentNodeNm()} nm gerechnet`;
  els['gpu-dialog'].showModal();
}

function applyGpuChoice() {
  if (state.gpuChoice) {
    state.spec.gpu_enabled = true;
    state.spec.gpu_id = state.gpuChoice;
  } else {
    state.spec.gpu_enabled = false;
    state.spec.gpu_id = null;
  }
  renderGpuSummary();
  hideConfig();
  scheduleEstimate();
}

// -------------------------------------------------------------- NPU

function renderNpus() {
  const sel = els['npu'];
  sel.innerHTML = '';
  (state.catalog.npus || []).forEach((n) => {
    const o = document.createElement('option');
    o.value = n.id;
    o.textContent = n.label;
    o.title = n.note || '';
    sel.appendChild(o);
  });
}

function syncNpuControls() {
  const on = els['npu-enabled'].checked;
  els['npu'].disabled = !on;
  els['npu-state'].textContent = on ? 'an' : 'aus';
  const npu = (state.catalog.npus || []).find((n) => n.id === els['npu'].value);
  if (!npu) { els['npu-hint'].textContent = ''; return; }
  const area = areaAtNode(npu.area28);
  els['npu-hint'].innerHTML =
    `<b>${esc(npu.family)}</b> · ${num(npu.macs_per_cycle, 0)} MACs/Zyklus · ` +
    `${num(area, 1)} mm² bei ${currentNodeNm()} nm` +
    (npu.note ? ` · ${esc(npu.note)}` : '');
}

// --------------------------------------------------------- Linux-Ziel

function renderLinuxStatus(est) {
  const box = els['linux-status'];
  if (!est || !est.linux) { box.innerHTML = ''; return; }
  const l = est.linux;
  els['linux-state'].textContent = els['linux-target'].checked ? 'an' : 'aus';
  if (l.ready) {
    box.className = 'linux-status ok';
    box.innerHTML = '<span class="linux-badge ok">Linux-fähig</span>' +
      '<span class="hint">RV64-Kerne, L3, DRAM, Konsole, Block-Gerät und ' +
      'Boot-Flash sind vorhanden.</span>';
    return;
  }
  box.className = 'linux-status bad';
  box.innerHTML = '<span class="linux-badge bad">nicht Linux-fähig</span>' +
    '<ul>' + l.issues.map((i) => `<li>${esc(i)}</li>`).join('') + '</ul>';
}

function renderRecommendations(est) {
  const box = els['recommendations'];
  const recs = (est && est.recommendations) || [];
  if (!recs.length) {
    box.innerHTML = '<span class="hint">Nichts zu ergänzen – der Aufbau ' +
      'entspricht einem modernen SoC.</span>';
    return;
  }
  box.innerHTML =
    `<div class="rec-head">Empfohlene Zusatzbausteine ` +
    `<button id="btn-apply-recs" class="btn btn-small" type="button">` +
    `Fehlendes ergänzen</button></div>` +
    '<ul>' + recs.map((r) =>
      `<li><b>${esc(r.label)}</b> – ${esc(r.why)}</li>`).join('') + '</ul>';
  const btn = document.getElementById('btn-apply-recs');
  if (btn) btn.addEventListener('click', applyRecommendations);
}

function applyRecommendations() {
  const est = state.estimate;
  if (!est || !est.recommendations) return;
  const modules = new Set(state.spec.modules);
  est.recommendations.forEach((r) => {
    switch (r.kind) {
      case 'l3': state.spec.l3_enabled = !!r.target; break;
      case 'l3_kib': state.spec.l3_kib = Number(r.target); break;
      case 'l3_banks': state.spec.l3_banks = Number(r.target); break;
      case 'dram_mib': state.spec.dram_mib = Number(r.target); break;
      case 'module': modules.add(r.target); break;
      case 'npu':
        state.spec.npu_enabled = true;
        state.spec.npu_id = state.spec.npu_id || 'gemmini_16';
        break;
      case 'gpu':
        state.spec.gpu_enabled = true;
        state.spec.gpu_id = state.spec.gpu_id || 'mali_g78';
        break;
      default: break;
    }
  });
  state.spec.modules = state.catalog.modules
    .map((m) => m.id).filter((id) => modules.has(id));
  fillFormFromSpec();
  renderGpuSummary();
  hideConfig();
  scheduleEstimate(0);
  toast('Fehlende Bausteine ergänzt', 'success');
}


function renderModules() {
  const box = els['modules'];
  box.innerHTML = '';
  const groups = new Map();
  state.catalog.modules.forEach((m) => {
    if (!groups.has(m.category)) groups.set(m.category, []);
    groups.get(m.category).push(m);
  });

  groups.forEach((mods, category) => {
    const g = document.createElement('div');
    g.className = 'mod-group';
    const t = document.createElement('div');
    t.className = 'mod-group-title';
    t.textContent = category;
    g.appendChild(t);

    mods.forEach((m) => {
      const onlyEstimate = !m.in_base &&
        (m.available === false || !m.fragment);
      const item = document.createElement('label');
      item.className = 'mod-item' + (onlyEstimate ? ' est-only' : '');
      item.dataset.module = m.id;
      item.title = m.note || '';

      const cb = document.createElement('input');
      cb.type = 'checkbox';
      cb.value = m.id;
      cb.checked = state.spec.modules.includes(m.id);
      cb.addEventListener('change', () => {
        const set = new Set(state.spec.modules);
        if (cb.checked) set.add(m.id); else set.delete(m.id);
        state.spec.modules = state.catalog.modules
          .map((x) => x.id).filter((id) => set.has(id));
        state.activePreset = null;
        document.querySelectorAll('.preset').forEach((b) => {
          b.classList.remove('active');
        });
        item.classList.toggle('on', cb.checked);
        hideConfig();
        renderSimPartners();
        scheduleEstimate();
      });

      const text = document.createElement('div');
      text.className = 'mod-text';
      const name = document.createElement('div');
      name.className = 'mod-name';
      const tag = m.in_base
        ? '<span class="tag base">im Basis-Config</span>'
        : onlyEstimate
          ? '<span class="tag est">nur Schätzung</span>'
          : '<span class="tag gen">wird ergänzt</span>';
      name.innerHTML = esc(m.label) + tag;
      const info = document.createElement('span');
      info.className = 'info-icon';
      info.textContent = 'i';
      info.title = m.note || 'Keine weitere Beschreibung verfügbar.';
      info.setAttribute('aria-label', info.title);
      info.setAttribute('tabindex', '0');
      name.appendChild(info);
      const note = document.createElement('div');
      note.className = 'mod-note';
      note.textContent = m.note || '';
      text.appendChild(name);
      if (m.note) text.appendChild(note);

      item.appendChild(cb);
      item.appendChild(text);
      item.classList.toggle('on', cb.checked);
      g.appendChild(item);
    });

    box.appendChild(g);
  });
}

// ------------------------------------------------- Simulationspartner

function renderSimPartners() {
  const box = els['sim-partners'];
  if (!box) return;
  const groups = new Map();
  (state.catalog.sim_partners || []).forEach((p) => {
    if (!groups.has(p.category)) groups.set(p.category, []);
    groups.get(p.category).push(p);
  });
  box.innerHTML = '';

  groups.forEach((items, category) => {
    const t = document.createElement('div');
    t.className = 'mod-group-title';
    t.textContent = category;
    box.appendChild(t);

    items.forEach((p) => {
      const missing = (p.requires || []).filter(
        (r) => !state.spec.modules.includes(r));
      const locked = !!p.in_base;            // Basis-Harness: immer an
      const selected = locked || state.spec.sim_partners.includes(p.id);
      const item = document.createElement('label');
      item.className = 'mod-item sim-item';
      if (locked) item.classList.add('locked');
      if (missing.length) item.classList.add('disabled');

      const cb = document.createElement('input');
      cb.type = 'checkbox';
      cb.value = p.id;
      cb.checked = selected;
      cb.disabled = locked || missing.length > 0;
      cb.addEventListener('change', () => {
        const set = new Set(state.spec.sim_partners);
        if (cb.checked) set.add(p.id); else set.delete(p.id);
        state.spec.sim_partners = (state.catalog.sim_partners || [])
          .map((x) => x.id).filter((id) => set.has(id));
        item.classList.toggle('on', cb.checked);
        hideConfig();
        scheduleEstimate();
      });

      const text = document.createElement('div');
      text.className = 'mod-text';
      const name = document.createElement('div');
      name.className = 'mod-name';
      name.innerHTML = esc(p.label) +
        (locked ? '<span class="tag base">Basis-Harness</span>' : '') +
        (missing.length
          ? `<span class="tag est">braucht Modul ${esc(missing.join(', '))}</span>`
          : '');
      const note = document.createElement('div');
      note.className = 'mod-note';
      note.textContent = p.note || '';
      text.append(name, note);

      item.append(cb, text);
      item.classList.toggle('on', cb.checked);
      box.appendChild(item);
    });
  });
}

function renderDramOptions() {
  const sel = els['dram-model'];
  sel.innerHTML = '';
  (state.catalog.dram_models || []).forEach((m) => {
    const o = document.createElement('option');
    o.value = m.id;
    o.textContent = m.label;
    o.title = m.note || '';
    sel.appendChild(o);
  });
  const size = els['dram-size'];
  size.innerHTML = '';
  (state.catalog.dram_size_choices || []).forEach((mib) => {
    const o = document.createElement('option');
    o.value = String(mib);
    o.textContent = mib >= 1024 ? (mib / 1024) + ' GiB' : mib + ' MiB';
    size.appendChild(o);
  });
}

function syncDramControls() {
  const model = els['dram-model'].value;
  const modelInfo = (state.catalog.dram_models || [])
    .find((m) => m.id === model);
  els['dram-latency'].disabled = model !== 'simdram';
  els['dram-hint'].textContent = modelInfo ? modelInfo.note : '';
}

async function loadWorkloads() {
  try {
    const data = await api('/workloads');
    state.workloads = data.workloads || [];
  } catch (_) {
    state.workloads = [];
  }
  const sel = els['workload'];
  sel.innerHTML = '';
  const groups = new Map();
  state.workloads.forEach((w) => {
    if (!groups.has(w.group)) groups.set(w.group, []);
    groups.get(w.group).push(w);
  });
  groups.forEach((items, group) => {
    const og = document.createElement('optgroup');
    og.label = group;
    items.forEach((w) => {
      const o = document.createElement('option');
      o.value = w.path;
      o.textContent = w.name;
      o.title = w.path;
      og.appendChild(o);
    });
    sel.appendChild(og);
  });
  if (!state.workloads.length) {
    const o = document.createElement('option');
    o.value = '';
    o.textContent = 'keine fertigen Programme gefunden';
    sel.appendChild(o);
  } else {
    els['workload-path'].value = state.workloads[0].path;
  }
}

// ---------------------------------------------------- Formular <-> Spec

function fillFormFromSpec() {
  const s = state.spec;
  if (!s.core_mix) s.core_mix = { [s.core_type]: s.cores };
  renderCoreMix();
  els['pdk'].value = s.pdk;
  els['l3-enabled'].checked = s.l3_enabled !== false;
  els['l3'].value = String(s.l3_kib || 512);
  els['l3-ways'].value = String(s.l3_ways || 8);
  els['l3-banks'].value = String(s.l3_banks || 1);
  els['npu-enabled'].checked = !!s.npu_enabled;
  if (s.npu_id) els['npu'].value = s.npu_id;
  els['linux-target'].checked = !!s.linux_target;
  els['rocc'].value = s.rocc_count;
  els['activity'].value = s.activity;
  els['cycles'].value = s.cycles_millions;
  els['dram-model'].value = s.dram_model || 'simdram';
  els['dram-size'].value = String(s.dram_mib || 256);
  if (els['dram-latency']) els['dram-latency'].value = s.dram_latency || 0;

  document.querySelectorAll('#modules input[type=checkbox]').forEach((cb) => {
    cb.checked = s.modules.includes(cb.value);
    cb.closest('.mod-item').classList.toggle('on', cb.checked);
  });
  document.querySelectorAll('#sim-partners input[type=checkbox]').forEach((cb) => {
    const p = (state.catalog.sim_partners || []).find((x) => x.id === cb.value);
    const selected = p && (p.in_base || s.sim_partners.includes(cb.value));
    cb.checked = !!selected;
    cb.closest('.mod-item').classList.toggle('on', !!selected);
  });

  syncL3Controls();
  syncDramControls();
  syncNpuControls();
  updateHints();
}

function readForm() {
  const s = state.spec;
  s.cores = coreTotal();
  s.core_type = primaryCore();
  s.pdk = els['pdk'].value;
  s.l3_enabled = els['l3-enabled'].checked;
  s.l3_kib = parseInt(els['l3'].value, 10) || 512;
  s.l3_ways = parseInt(els['l3-ways'].value, 10) || 8;
  s.l3_banks = parseInt(els['l3-banks'].value, 10) || 1;
  s.npu_enabled = els['npu-enabled'].checked;
  s.npu_id = els['npu'].value || 'gemmini_16';
  s.linux_target = els['linux-target'].checked;
  s.rocc_count = clampInt(els['rocc'].value, 0, 8, 0);
  s.activity = Math.min(1, Math.max(0.05, parseFloat(els['activity'].value) || 1));
  s.cycles_millions = Math.max(1, parseFloat(els['cycles'].value) || 50);
  s.dram_model = els['dram-model'].value;
  s.dram_mib = parseInt(els['dram-size'].value, 10) || 256;
  s.dram_latency = clampInt(els['dram-latency'].value, 0, 64, 0);
  s.build_system = 'verilator';
  return s;
}

function updateHints() {
  const s = readForm();
  const mixLabel = Object.entries(s.core_mix).map(([id, count]) => {
    const core = state.catalog.core_types.find((item) => item.id === id);
    const name = core ? core.label.split(' -- ')[0] : id;
    return `${count} × ${name}`;
  }).join(' · ');
  els['cores-hint'].textContent = `${mixLabel}. Gesamt ${s.cores} / 64 Kerne; ` +
    `Fläche und Simulationsdauer steigen mit Zahl und Komplexität der Kerne.`;
  const pdk = state.catalog.presets.find((p) => p.id === s.pdk);

  if (pdk) {
    els['pdk-hint'].innerHTML =
      `<b>${esc(pdk.kind)}</b> · ${pdk.node_nm} nm · ` +
      `${num(pdk.vdd, 1)} V · f<sub>max</sub> ${num(pdk.fmax_ghz, 2)} GHz`;
  }
  const bits = new Set(Object.keys(s.core_mix).map((id) => {
    const core = state.catalog.core_types.find((c) => c.id === id);
    return core ? core.bits || 64 : 64;
  }));
  if (bits.size > 1) {
    els['cores-hint'].innerHTML += ' <b>RV32 und RV64 gemischt – ' +
      'das ergibt keinen sinnvollen Chip.</b>';
  }
  syncL3Controls();
  syncDramControls();
  syncNpuControls();
}

// ------------------------------------------------------------ Schätzung

let estimateTimer = null;
function scheduleEstimate(delay = 220) {
  clearTimeout(estimateTimer);
  estimateTimer = setTimeout(runEstimate, delay);
}

async function runEstimate() {
  const spec = readForm();
  updateHints();
  const myVersion = ++state.version;
  try {
    const data = await post('/estimate', spec);
    if (myVersion !== state.version) return;   // veraltete Antwort verwerfen
    state.estimate = data;
    renderFloorplan(data.floorplan, data.spec);
    renderCards(data);
    renderAssumptions(data.assumptions);
    renderLayoutSummary(data);
    renderPlainSummary(data);
    renderLinuxStatus(data);
    renderRecommendations(data);
    renderFooter(data);
    renderPreviewCfg(data.config_preview);
    clearError();
  } catch (err) {
    if (myVersion !== state.version) return;
    showError('Schätzung fehlgeschlagen: ' + err.message);
  }
}

// --- Floorplan als SVG -----------------------------------------------------

const KINDS = {
  cores:  { fill: '#2f6fdb', stroke: '#5b95ef', label: 'Kerne' },
  memory: { fill: '#0f766e', stroke: '#2dd4bf', label: 'L3 / LLC' },
  uncore: { fill: '#475569', stroke: '#7c8ea8', label: 'Uncore / Busse' },
  accel:  { fill: '#9d174d', stroke: '#f472b6', label: 'Beschleuniger' },
  periph: { fill: '#15803d', stroke: '#4ade80', label: 'Peripherie' },
  gpu:    { fill: '#7c3aed', stroke: '#a78bfa', label: 'GPU' },
};

const SVG_NS = 'http://www.w3.org/2000/svg';
const PAD_PX = 16;
const VIEW_W = 440;

function svgEl(tag, attrs) {
  const el = document.createElementNS(SVG_NS, tag);
  for (const k in attrs) el.setAttribute(k, attrs[k]);
  return el;
}

function renderFloorplan(fp, spec) {
  const svg = els['floorplan'];
  svg.innerHTML = '';
  if (!fp || !fp.blocks) return;

  const dieW = fp.die_w, dieH = fp.die_h;
  const scale = (VIEW_W - 2 * PAD_PX) / dieW;
  const viewH = dieH * scale + 2 * PAD_PX;
  svg.setAttribute('viewBox', `0 0 ${VIEW_W} ${viewH}`);
  svg.setAttribute('preserveAspectRatio', 'xMidYMid meet');

  // Offset: Inhalt innerhalb des Die zentrieren (MIN_DIE_SIDE kann auffüllen)
  const offX = (dieW - fp.content_w) / 2;
  const offY = (dieH - fp.content_h) / 2;
  const X = (mm) => (mm + offX) * scale + PAD_PX;
  const Y = (mm) => (mm + offY) * scale + PAD_PX;
  const S = (mm) => mm * scale;

  // Die-Rahmen
  svg.appendChild(svgEl('rect', {
    class: 'die', x: PAD_PX, y: PAD_PX,
    width: S(dieW), height: S(dieH), rx: 3,
  }));

  // IO-Ring andeuten
  const ring = fp.io_ring || 0;
  if (ring > 0 && S(dieW) > 60) {
    svg.appendChild(svgEl('rect', {
      class: 'ring', x: PAD_PX + S(ring), y: PAD_PX + S(ring),
      width: S(dieW - 2 * ring), height: S(dieH - 2 * ring), rx: 2,
    }));
  }

  const used = new Set();
  fp.blocks.forEach((b) => {
    const kind = KINDS[b.kind] ? b.kind : 'uncore';
    const k = KINDS[kind];
    used.add(kind);
    const x = X(b.x), y = Y(b.y), w = S(b.w), h = S(b.h);

    const g = svgEl('g', {});
    const title = `${b.label} — ${num(b.area, 4)} mm²`;
    const tip = svgEl('title', {});
    tip.textContent = title;
    g.appendChild(tip);

    g.appendChild(svgEl('rect', {
      class: 'block', x, y, width: w, height: h, rx: 2,
      fill: k.fill, stroke: k.stroke,
      'fill-opacity': 0.9,
    }));

    // Kern-Cluster: Kachelraster andeuten
    if (b.kind === 'cores' && b.cols > 1) {
      for (let i = 1; i < b.cols; i++) {
        const lx = x + (w / b.cols) * i;
        g.appendChild(svgEl('line', {
          class: 'tile-line', x1: lx, y1: y, x2: lx, y2: y + h,
        }));
      }
      for (let j = 1; j < b.rows; j++) {
        const ly = y + (h / b.rows) * j;
        g.appendChild(svgEl('line', {
          class: 'tile-line', x1: x, y1: ly, x2: x + w, y2: ly,
        }));
      }
    }

    // Beschriftung nur, wenn Platz ist
    const coreLabel = (state.catalog.core_types.find((core) =>
      core.id === b.core_type) || {}).label || 'Kern';
    const label = b.kind === 'cores'
      ? `${b.cores} × ${coreLabel.split(' -- ')[0]}`
      : b.label;
    if (w > 42 && h > 20) {
      const t1 = svgEl('text', {
        class: 'block-label', x: x + 4, y: y + 12,
      });
      t1.textContent = label.length > 22 ? label.slice(0, 21) + '…' : label;
      g.appendChild(t1);
      if (h > 32) {
        const t2 = svgEl('text', {
          class: 'block-area', x: x + 4, y: y + 22,
        });
        t2.textContent = num(b.area, 4) + ' mm²';
        g.appendChild(t2);
      }
    }
    svg.appendChild(g);
  });

  // Legende
  const lg = els['legend'];
  lg.innerHTML = '';
  ['cores', 'memory', 'gpu', 'accel', 'uncore', 'periph'].forEach((kind) => {
    if (!used.has(kind)) return;
    const li = document.createElement('li');
    li.innerHTML = `<span class="swatch" style="background:${KINDS[kind].fill};` +
      `border-color:${KINDS[kind].stroke}"></span>${KINDS[kind].label}`;
    lg.appendChild(li);
  });
}

function renderLayoutSummary(data) {
  const a = data.area, fp = data.floorplan;
  els['layout-summary'].innerHTML =
    `Die <b>${num(a.die_w_mm, 2)} × ${num(a.die_h_mm, 2)} mm</b> ` +
    `(${num(a.die_mm2, 2)} mm²) · Logik ${num(a.logic_mm2, 2)} mm² · ` +
    `Auslastung <b>${num(a.utilization * 100, 0)} %</b> · ` +
    `${fp.blocks.length} Blöcke, IO-Ring ${num(fp.io_ring * 1000, 0)} µm`;
}

function renderPlainSummary(d) {
  const s = d.spec, a = d.area, p = d.power, sim = d.sim;
  const items = [];
  const mix = Object.entries(s.core_mix || {}).map(([id, count]) => {
    const core = state.catalog.core_types.find((c) => c.id === id);
    return `${count} × ${core ? core.label.split(' -- ')[0] : id}`;
  }).join(' + ');
  items.push(`<b>${esc(mix)}</b> auf ${esc(d.pdk.label)}.`);
  items.push(s.l3_enabled
    ? `L3-Cache: ${s.l3_kib} KiB, ${s.l3_ways}-fach, ${s.l3_banks} Bank(s).`
    : 'Ohne L3-Cache (nur Kohärenz-Hub).');
  if (d.gpu) {
    items.push(`GPU: <b>${esc(d.gpu.label)}</b> – ${num(d.gpu.area_mm2, 1)} mm², ` +
      `${num(d.gpu.gflops / 1000, 1)} TFLOP/s (nur Schätzung, nicht im Verilog).`);
  }
  if (d.npu) {
    items.push(`NPU: <b>${esc(d.npu.label)}</b> – ${num(d.npu.area_mm2, 1)} mm², ` +
      `${num(d.perf.npu_macs, 0)} MACs/Zyklus (wird als Fragment gebaut).`);
  }
  items.push(d.linux.ready
    ? 'Der Chip ist <b>Linux-fähig</b>: RV64-Kerne, L3, genug DRAM, ' +
      'Konsole, Block-Gerät und Boot-Flash sind vorhanden.'
    : `Für Linux fehlt noch: ${esc(d.linux.issues[0])}` +
      (d.linux.issues.length > 1 ? ` (und ${d.linux.issues.length - 1} weitere)` : ''));
  items.push(`Die-Fläche ${num(a.die_mm2, 1)} mm² – das ist ` +
    `${a.die_mm2 < 25 ? 'kleiner als ein Fingernagel' :
       a.die_mm2 < 100 ? 'etwa ein Fingernagel bis Daumenabdruck' :
       a.die_mm2 < 400 ? 'etwa eine Briefmarke' :
       'deutlich größer als eine Briefmarke'}.`);
  items.push(`Stromverbrauch rund ${num(p.total_w, 1)} W bei ` +
    `${num(p.freq_ghz, 2)} GHz.`);
  items.push(`Simulation mit Verilator: grob ${duration(sim.verilator_wall_hours)} ` +
    `für ${num(sim.cycles_millions, 0)} Mio. Zyklen, ` +
    `${num(sim.host_ram_gb, 1)} GB Host-RAM (${sim.dram_mib} MiB DRAM, ` +
    `${sim.dram_model === 'simdram' ? 'SimDRAM' : 'SimAXIMem'}).`);
  els['plain-summary'].innerHTML = items.map((t) => `<li>${t}</li>`).join('');
}

// --- Kennzahlen-Karten ----------------------------------------------------

function renderCards(d) {
  const a = d.area, p = d.power, pf = d.perf, w = d.wafer, s = d.sim;
  const jobs = clampInt(els['jobs'].value, 1, 32, 4);
  const cards = [];

  cards.push({
    title: 'Die-Fläche',
    value: `${num(a.die_w_mm, 2)} × ${num(a.die_h_mm, 2)} mm`,
    sub: `${num(a.die_mm2, 3)} mm² · Auslastung ${num(a.utilization * 100, 0)} %`,
    cls: 'accent',
  });

  cards.push({
    title: 'Ziel-Takt',
    value: `${num(p.freq_ghz, 2)} GHz`,
    sub: `${d.pdk.node_nm} nm · ${num(p.vdd, 2)} V`,
  });

  cards.push({
    title: 'Leistung',
    value: `${num(p.total_w, 2)} W`,
    sub: `dyn ${num(p.dynamic_w, 2)} W + leak ${num(p.leakage_w, 2)} W`,
    cls: p.power_density_w_per_mm2 > 0.25 ? 'warn' : '',
  });

  cards.push({
    title: 'Rechenleistung',
    value: `${num(pf.dmips, 0)} DMIPS`,
    sub: `${num(pf.gflops_core, 2)} GFLOP/s (FP)`,
  });

  if (pf.macs_total > 0) {
    cards.push({
      title: 'Beschleuniger',
      value: `${num(pf.tops_accel, 2)} TFLOP/s`,
      sub: `${num(pf.macs_total, 0)} MACs/Zyklus`,
    });
  }

  if (pf.gflops_gpu > 0) {
    cards.push({
      title: 'GPU-Leistung',
      value: `${num(pf.tops_gpu, 2)} TFLOP/s`,
      sub: `nur Schätzung · ${num(a.gpu_mm2, 1)} mm²`,
      cls: 'warn',
    });
  }

  if (d.npu) {
    cards.push({
      title: 'NPU',
      value: `${num(pf.npu_macs * 2 * p.freq_ghz / 1000, 2)} TOPS`,
      sub: `${esc(d.npu.family)} · ${num(pf.npu_macs, 0)} MACs/Zyklus · ` +
           `${num(a.npu_mm2, 1)} mm²`,
    });
  }

  cards.push({
    title: 'Linux',
    value: d.linux.ready ? 'fähig' : 'nein',
    sub: d.linux.ready
      ? `RV${d.linux.bits.join('/')} · ${num(d.linux.dram_mib, 0)} MiB DRAM`
      : `${d.linux.issues.length} Punkt(e) offen`,
    cls: d.linux.ready ? 'ok-card' : 'warn',
  });

  cards.push({
    title: 'Wafer-Ausbeute',
    value: `${num(w.good_dies, 0)} gute Dies`,
    sub: `${num(w.dies_per_wafer, 0)} brutto · ${num(w.yield_pct * 100, 1)} %`,
  });

  cards.push({
    title: 'Simulation (Verilator)',
    value: duration(s.verilator_wall_hours),
    sub: `${num(s.verilator_throughput_mcyc_s, 2)} Mcyc/s · ` +
         `${num(s.cycles_millions, 0)} Mio. Zyklen`,
    cls: 'accent',
  });

  cards.push({
    title: 'Host-Bedarf',
    value: `${num(s.host_ram_gb, 1)} GB RAM`,
    sub: `Bau grob ${num(s.build_minutes_estimate, 0)} min · -j${jobs}`,
  });

  cards.push({
    title: 'DRAM (Simulation)',
    value: `${num(s.dram_mib, 0)} MiB`,
    sub: `${s.dram_model === 'simdram' ? 'SimDRAM (Timing)' : 'SimAXIMem (1 Zyklus)'}`,
  });

  cards.push({
    title: 'Simulation (FireSim)',
    value: duration(s.firesim_wall_hours),
    sub: `${num(s.firesim_sim_mhz, 0)} MHz-Modell auf FPGA`,
  });

  els['cards'].innerHTML = cards.map((c) => `
    <div class="card ${c.cls}">
      <div class="card-title">${esc(c.title)}</div>
      <div class="card-value">${c.value}</div>
      <div class="card-sub">${esc(c.sub)}</div>
    </div>`).join('');

  const hostRam = Number(state.catalog.host_memory_gb);
  const warning = els['resource-warning'];
  if (hostRam > 0 && s.host_ram_gb >= hostRam * 0.85) {
    warning.textContent = `Speicherwarnung: Für Build/Simulation werden grob ${num(s.host_ram_gb, 1)} GB RAM geschätzt; ` +
      `dieser Rechner hat ${num(hostRam, 1)} GB. Der Build kann wegen Speichermangels abbrechen.`;
    warning.hidden = false;
  } else {
    warning.hidden = true;
    warning.textContent = '';
  }
}

function renderAssumptions(list) {
  els['assumptions'].innerHTML = (list || [])
    .map((x) => `<li>${esc(x)}</li>`).join('');
}

function renderFooter(d) {
  const coreSummary = Object.entries(d.spec.core_mix || {}).map(([id, count]) => {
    const core = state.catalog.core_types.find((item) => item.id === id);
    return `${count} × ${core ? core.label.split(' -- ')[0] : id}`;
  }).join(' + ');
  els['foot-est'].innerHTML =
    `<b>${esc(coreSummary)}</b> @ ` +
    `${esc(d.pdk.label)} · Die ${num(d.area.die_w_mm, 2)}×` +
    `${num(d.area.die_h_mm, 2)} mm · ${num(d.power.total_w, 2)} W · ` +
    `${num(d.wafer.good_dies, 0)} gute Dies/Wafer · ` +
    `Verilator ${duration(d.sim.verilator_wall_hours)}`;
}

function renderPreviewCfg(src) {
  const pre = els['config-source'];
  if (pre.hidden === false) pre.textContent = src || '';
}

// -------------------------------------------------------------- Config

async function showConfig(write) {
  const spec = readForm();
  try {
    const data = await api('/config' + (write ? '?write=1' : ''), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(spec),
    });
    els['config-source'].textContent = data.source;
    els['config-source'].hidden = false;
    renderWarnings(data.warnings);
    if (write) {
      els['target-path'].textContent = data.written || els['target-path'].textContent;
      toast('Config abgelegt: ' + data.written, 'success');
    } else {
      toast('Config-Vorschau erzeugt', 'info', 2500);
    }
  } catch (err) {
    showError('Config konnte nicht erzeugt werden: ' + err.message);
  }
}

function hideConfig() {
  els['config-source'].hidden = true;
  els['warnings'].hidden = true;
}

function renderWarnings(list) {
  const box = els['warnings'];
  const onlyEst = state.catalog.modules
    .filter((m) => state.spec.modules.includes(m.id) && !m.in_base &&
                   (m.available === false || !m.fragment))
    .map((m) => `${m.label} wird nur geschätzt: in diesem Chipyard-Checkout ` +
                `gibt es dafür kein fertiges Config-Fragment, es erscheint ` +
                `also nicht im generierten Verilog.`);
  if (state.spec.gpu_enabled && state.spec.gpu_id) {
    const gpu = (state.catalog.gpus || []).find((g) => g.id === state.spec.gpu_id);
    if (gpu && !gpu.fragment) {
      onlyEst.push(`${gpu.label} wird nur geschätzt: ohne GPU-Generator gibt ` +
                   `es kein Config-Fragment, die GPU erscheint nicht im Verilog.`);
    }
  }
  // Der Generator meldet dieselben Module in eigenen Worten -- nicht doppeln.
  const fromServer = (list || []).filter(
    (w) => !/nur in der Schaetzung beruecksichtigt/.test(w));
  const seen = new Set();
  const all = [...fromServer, ...onlyEst].filter((w) => {
    if (seen.has(w)) return false;
    seen.add(w);
    return true;
  });
  if (!all.length) { box.hidden = true; box.innerHTML = ''; return; }
  box.hidden = false;
  box.classList.remove('errors');
  box.innerHTML = '<b>Hinweise</b><ul>' +
    all.map((w) => `<li>${esc(w)}</li>`).join('') + '</ul>';
}

function showError(msg) {
  const box = els['warnings'];
  box.hidden = false;
  box.classList.add('errors');
  box.textContent = msg;
}

function clearError() {
  const box = els['warnings'];
  if (box.classList.contains('errors')) {
    box.classList.remove('errors');
    box.hidden = true;
    box.textContent = '';
  }
}

// --------------------------------------------------------------- Build

async function startBuild() {
  const spec = readForm();
  const jobs = clampInt(els['jobs'].value, 1, 32, 4);
  els['btn-build'].disabled = true;
  els['build-panel'].hidden = false;
  setBadge('build', 'running', 'Startet');
  els['build-phase'].textContent = 'Config wird geschrieben …';
  els['build-log'].textContent = '';
  els['build-diagnostic'].hidden = true;
  els['file-list'].innerHTML = '';
  els['btn-zip'].hidden = true;

  try {
    const data = await post('/build', {
      ...spec, jobs, chipyard_root: state.catalog.chipyard.root,
    });
    state.jobId = data.job.id;
    state.jobKind = 'verilog';
    toast('Build gestartet: ' + state.jobId, 'info');
    pollJob();
  } catch (err) {
    setBadge('build', 'failed', 'Fehler');
    els['build-phase'].textContent = '';
    showError('Build konnte nicht gestartet werden: ' + err.message);
    els['btn-build'].disabled = false;
  }
}

const PHASES = {
  prepare: 'Config wird geschrieben',
  build: 'Scala/FIRRTL-Build läuft',
  compile: 'C++-Simulator wird übersetzt',
  run: 'Simulation läuft',
  collect: 'Ergebnisse werden eingesammelt',
  done: 'abgeschlossen',
};

function setBadge(panel, cls, text) {
  const b = els[panel === 'sim' ? 'sim-badge' : 'build-badge'];
  b.className = 'badge ' + cls;
  b.textContent = text;
}

function renderDiagnostic(panel, job) {
  const details = job.diagnostic || {};
  const pre = panel === 'sim' ? 'sim' : 'build';
  els[pre + '-error-reason'].textContent = details.reason || job.error ||
    'Der Lauf ist ohne erfolgreiches Ergebnis beendet worden.';
  els[pre + '-error-location'].textContent = details.location ||
    `Phase: ${PHASES[job.phase] || job.phase || 'unbekannt'}; Details stehen im Log.`;
  els[pre + '-error-hint'].textContent = details.suggestion ||
    'Öffne den Log-Ausschnitt und suche die erste Fehlermeldung.';
  els[pre + '-diagnostic'].hidden = false;
}

async function pollJob() {
  clearTimeout(state.jobTimer);
  const id = state.jobId;
  if (!id) return;
  try {
    const data = await api('/build/' + encodeURIComponent(id));
    const job = data.job;
    if (job.id !== state.jobId) return;   // inzwischen anderer Job
    const panel = job.kind === 'sim' ? 'sim' : 'build';
    const pre = panel === 'sim' ? 'sim' : 'build';

    els[pre + '-phase'].textContent = PHASES[job.phase] || job.phase || '';
    els[pre + '-elapsed'].textContent = secs(job.elapsed_s);
    if (job.log_tail) {
      const log = els[pre + '-log'];
      log.textContent = job.log_tail;
      log.scrollTop = log.scrollHeight;
    }

    if (job.status === 'running') {
      setBadge(panel, 'running', 'Läuft');
      state.jobTimer = setTimeout(pollJob, 1500);
      return;
    }

    if (panel === 'sim') {
      els['btn-sim-run'].disabled = false;
      els['btn-sim-build'].disabled = false;
    } else {
      els['btn-build'].disabled = false;
    }

    if (job.status === 'done') {
      setBadge(panel, 'done', 'Fertig');
      await loadArtifacts(id, job);
      toast(panel === 'sim' ? 'Simulation abgeschlossen' :
        'Verilog erfolgreich erzeugt', 'success');
      loadJobs();
    } else {
      setBadge(panel, 'failed', 'Fehlgeschlagen');
      renderDiagnostic(panel, job);
      if (job.error) showError(job.error);
      toast(panel === 'sim' ? 'Simulation fehlgeschlagen' :
        'Build fehlgeschlagen', 'error', 6000);
      loadJobs();
    }
  } catch (err) {
    setBadge('build', 'failed', 'Fehler');
    showError('Status konnte nicht abgefragt werden: ' + err.message);
    els['btn-build'].disabled = false;
    els['btn-sim-run'].disabled = false;
    els['btn-sim-build'].disabled = false;
  }
}

async function loadArtifacts(id, job) {
  const isSim = job && job.kind === 'sim';
  const pre = isSim ? 'sim' : 'build';
  try {
    const data = await api('/build/' + encodeURIComponent(id) + '/files');
    const files = data.files || [];
    const ul = els[pre + '-files'] || els['file-list'];
    ul.innerHTML = '';
    const MAX = 300;
    files.slice(0, MAX).forEach((f) => {
      const href = `${API}/build/${encodeURIComponent(id)}/file/` +
        f.name.split('/').map(encodeURIComponent).join('/');
      const li = document.createElement('li');
      li.className = 'file-item';
      li.innerHTML =
        `<a href="${href}" target="_blank" rel="noopener">${esc(f.name)}</a>` +
        `<span class="size">${bytes(f.size)}</span>`;
      ul.appendChild(li);
    });
    if (files.length > MAX) {
      const li = document.createElement('li');
      li.className = 'muted';
      li.textContent = `… und ${files.length - MAX} weitere Dateien ` +
        `(alle im ZIP)`;
      ul.appendChild(li);
    }
    if (!files.length) {
      ul.innerHTML = `<li class="muted">${isSim
        ? 'Keine Simulations-Ausgabedateien gefunden.'
        : 'Keine Verilog-Dateien gefunden.'}</li>`;
    } else {
      const zip = els[isSim ? 'sim-zip' : 'btn-zip'];
      zip.hidden = false;
      zip.href = `${API}/build/${encodeURIComponent(id)}/zip`;
    }
    const rtl = data.rtl_files ?? (job && job.verilog
      ? job.verilog.rtl_files : null);
    els[pre + '-phase'].textContent =
      `${files.length} Dateien` + (rtl ? `, davon ${rtl} RTL` : '') +
      (data.total_bytes ? ` · ${bytes(data.total_bytes)}` : '');
  } catch (err) {
    toast('Dateiliste nicht abrufbar: ' + err.message, 'warning');
  }
}

// -------------------------------------------------------------- Simulation

async function startSimulation(run) {
  const spec = readForm();
  const jobs = clampInt(els['jobs'].value, 1, 32, 4);
  const workload = (els['workload-path'].value || '').trim();
  const timeout_s = clampInt(els['sim-timeout'].value, 10, 86400, 3600);
  els['btn-sim-run'].disabled = true;
  els['btn-sim-build'].disabled = true;
  els['sim-panel'].hidden = false;
  setBadge('sim', 'running', run ? 'Startet' : 'Baut');
  els['sim-phase'].textContent = 'Config wird geschrieben …';
  els['sim-log'].textContent = '';
  els['sim-diagnostic'].hidden = true;
  els['sim-files'].innerHTML = '';
  els['sim-zip'].hidden = true;

  try {
    const data = await post('/simulate', {
      ...spec, jobs,
      run,
      workload,
      sim_flags: (els['sim-flags'].value || '').trim(),
      timeout_s,
      chipyard_root: state.catalog.chipyard.root,
    });
    state.jobId = data.job.id;
    state.jobKind = 'sim';
    pollJob();
  } catch (err) {
    setBadge('sim', 'failed', 'Fehler');
    els['sim-phase'].textContent = '';
    showError('Simulation konnte nicht gestartet werden: ' + err.message);
    els['btn-sim-run'].disabled = false;
    els['btn-sim-build'].disabled = false;
  }
}

// --------------------------------------------------------- Frühere Läufe

async function loadJobs() {
  const ul = els['job-list'];
  try {
    const data = await api('/jobs');
    const jobs = data.jobs || [];
    if (!jobs.length) {
      ul.innerHTML = '<li class="muted">noch keine</li>';
      return;
    }
    ul.innerHTML = '';
    jobs.slice(0, 14).forEach((j) => {
      const li = document.createElement('li');
      li.className = 'job-item';
      const cls = j.status === 'done' ? 'done'
        : j.status === 'running' ? 'running' : 'failed';
      const when = j.started
        ? new Date(j.started * 1000).toLocaleString('de-DE')
        : '?';
      const isSim = j.kind === 'sim';
      const detail = isSim
        ? `${esc(j.workload ? j.workload.split('/').pop() : 'Simulation')}` +
          (j.exit_code !== null && j.exit_code !== undefined
            ? ` · Exit ${j.exit_code}` : '')
        : `${num(j.cores, 0)} × ${esc(j.core_type || '?')}`;
      li.innerHTML =
        `<span class="badge ${cls}">${esc(j.status)}</span>` +
        `<span class="tag ${isSim ? 'gen' : 'base'}">${isSim ? 'Sim' : 'RTL'}</span>` +
        `<span class="job-meta">` +
        `<span class="job-spec">${detail} · ${esc(j.pdk || '?')}` +
        `${j.has_verilog ? ' · Verilog ✓' : ''}` +
        `${j.has_sim ? ' · Ausgabe ✓' : ''}</span>` +
        `<span class="job-id">${esc(when)} · ${esc(j.id)}</span>` +
        `</span>`;
      li.addEventListener('click', () => openJob(j.id, isSim));
      ul.appendChild(li);
    });
  } catch (err) {
    ul.innerHTML = `<li class="muted">Historie nicht ladbar: ${esc(err.message)}</li>`;
  }
}

async function openJob(id, isSim) {
  state.jobId = id;
  state.jobKind = isSim ? 'sim' : 'verilog';
  const pre = isSim ? 'sim' : 'build';
  els[pre + '-panel'].hidden = false;
  setBadge(pre, 'running', '…');
  els[pre + '-phase'].textContent = 'Lade Lauf ' + id;
  els[pre + '-diagnostic'].hidden = true;
  try {
    const data = await api('/build/' + encodeURIComponent(id));
    const job = data.job;
    els[pre + '-log'].textContent = job.log_tail || '';
    els[pre + '-elapsed'].textContent = secs(job.elapsed_s);
    els[pre + '-phase'].textContent = PHASES[job.phase] || job.phase || '';
    if (job.status === 'done') {
      setBadge(pre, 'done', 'Fertig');
      await loadArtifacts(id, job);
    } else if (job.status === 'running') {
      setBadge(pre, 'running', 'Läuft');
      pollJob();
    } else {
      setBadge(pre, 'failed', 'Fehlgeschlagen');
      renderDiagnostic(pre, job);
      if (job.error) showError(job.error);
      (els[pre + '-files'] || els['file-list']).innerHTML = '';
    }
  } catch (err) {
    setBadge(pre, 'failed', 'Fehler');
    showError('Lauf nicht ladbar: ' + err.message);
  }
}

// ---------------------------------------------------------------- Theme

/* Farbschemata: Reihenfolge = Auswahlfeld. Jedes Theme braucht einen
   passenden Block in style.css (html[data-theme="…"]). */
const THEMES = [
  { id: 'dark', label: 'Dunkel (Standard)' },
  { id: 'steffes', label: 'Schwarz · Cyan (Marke)' },
  { id: 'cyan', label: 'Dunkel · Cyan' },
  { id: 'midnight', label: 'Mitternacht · Navy' },
  { id: 'carbon', label: 'Carbon · Anthrazit' },
  { id: 'matrix', label: 'Matrix · Grün' },
  { id: 'amber', label: 'Amber · Warm' },
  { id: 'light', label: 'Hell · Blau' },
  { id: 'daylight', label: 'Tageslicht · Weiß' },
  { id: 'paper', label: 'Papier · Warm' },
];

function renderThemeOptions() {
  const sel = els['theme'];
  if (!sel) return;
  sel.innerHTML = '';
  THEMES.forEach((t) => {
    const o = document.createElement('option');
    o.value = t.id;
    o.textContent = t.label;
    sel.appendChild(o);
  });
}

function applyTheme(theme) {
  const allowed = THEMES.map((t) => t.id);
  const selected = allowed.includes(theme) ? theme : 'dark';
  document.documentElement.dataset.theme = selected;
  if (els['theme']) els['theme'].value = selected;
  try { localStorage.setItem('chipshop-theme', selected); } catch (_) { /* optional */ }
}

// ------------------------------------------------------------- Projekte

function saveProject() {
  const payload = {
    format: 'chipshop-project',
    version: 2,
    saved_at: new Date().toISOString(),
    spec: { ...readForm(), core_mix: { ...state.spec.core_mix },
      modules: [...state.spec.modules],
      sim_partners: [...state.spec.sim_partners] },
  };
  const blob = new Blob([JSON.stringify(payload, null, 2)],
    { type: 'application/json' });
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = 'chipshop-projekt.json';
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
  toast('Projekt als JSON gespeichert', 'success');
}

function loadProjectData(payload) {
  const source = payload && (payload.spec || (payload.project && payload.project.spec) || payload);
  if (!source || typeof source !== 'object' || Array.isArray(source)) {
    throw new Error('Die Datei enthält keine ChipShop-Konfiguration.');
  }
  const availableTypes = new Set(state.catalog.core_types.map((core) => core.id));
  const rawMix = source.core_mix || { [source.core_type || 'rocket']: source.cores || 1 };
  if (!rawMix || typeof rawMix !== 'object' || Array.isArray(rawMix)) {
    throw new Error('Die Kernauswahl in der Projektdatei ist ungültig.');
  }
  const coreMix = {};
  Object.entries(rawMix).forEach(([id, value]) => {
    const count = Number(value);
    if (!availableTypes.has(id) || !Number.isInteger(count) || count < 0) {
      throw new Error(`Ungültiger Kerntyp oder ungültige Anzahl: ${id}.`);
    }
    if (count) coreMix[id] = count;
  });
  const total = coreTotal(coreMix);
  if (total < 1 || total > 64) {
    throw new Error('Die Gesamtzahl der Kerne muss zwischen 1 und 64 liegen.');
  }
  const moduleIds = new Set(state.catalog.modules.map((module) => module.id));
  const partnerIds = new Set((state.catalog.sim_partners || []).map((p) => p.id));
  const gpuIds = new Set((state.catalog.gpus || []).map((g) => g.id));
  const npuIds = new Set((state.catalog.npus || []).map((n) => n.id));
  const gpuEnabled = !!source.gpu_enabled && gpuIds.has(source.gpu_id);
  const npuEnabled = !!source.npu_enabled && npuIds.has(source.npu_id);
  state.spec = {
    ...state.catalog.defaults,
    ...source,
    core_mix: coreMix,
    cores: total,
    core_type: primaryCore(coreMix),
    gpu_enabled: gpuEnabled,
    gpu_id: gpuEnabled ? source.gpu_id : null,
    npu_enabled: npuEnabled,
    npu_id: npuEnabled ? source.npu_id
      : (state.catalog.defaults.npu_id || 'gemmini_16'),
    linux_target: !!source.linux_target,
    modules: (Array.isArray(source.modules) ? source.modules : [])
      .filter((id) => moduleIds.has(id)),
    sim_partners: (Array.isArray(source.sim_partners) ? source.sim_partners
      : state.catalog.defaults.sim_partners)
      .filter((id) => partnerIds.has(id)),
  };
  state.activePreset = null;
  document.querySelectorAll('.preset').forEach((button) => button.classList.remove('active'));
  fillFormFromSpec();
  renderGpuSummary();
  hideConfig();
  scheduleEstimate(0);
  toast('Projekt geladen', 'success');
}

async function readProjectFile(file) {
  if (!file) return;
  try {
    loadProjectData(JSON.parse(await file.text()));
  } catch (err) {
    showError('Projekt konnte nicht geladen werden: ' + err.message);
  } finally {
    els['project-file'].value = '';
  }
}

// ----------------------------------------------------------- Verdrahtung

function bind() {
  ['chipyard-info', 'preset-row', 'core-list', 'core-total', 'btn-add-core',
   'cores-hint', 'pdk', 'pdk-hint', 'l3', 'l3-ways', 'l3-banks', 'l3-enabled',
   'l3-state', 'l3-hint', 'gpu-summary', 'gpu-tag', 'btn-gpu-pick',
   'btn-gpu-clear', 'gpu-dialog', 'gpu-grid', 'gpu-dialog-hint',
   'btn-gpu-apply', 'btn-help', 'help-dialog', 'modules', 'rocc', 'activity',
   'npu', 'npu-enabled', 'npu-state', 'npu-hint', 'linux-target',
   'linux-state', 'linux-status', 'recommendations',
   'cycles', 'jobs', 'floorplan', 'legend', 'layout-summary', 'plain-summary',
   'cards', 'resource-warning', 'assumptions', 'target-path', 'btn-config',
   'btn-write', 'btn-build', 'warnings', 'config-source', 'build-panel',
   'build-badge', 'build-phase', 'build-elapsed', 'build-log',
   'build-diagnostic', 'build-error-reason', 'build-error-location',
   'build-error-hint', 'btn-zip', 'btn-cancel-view', 'file-list', 'job-list',
   'foot-est', 'btn-settings', 'settings-panel', 'theme', 'btn-save-project',
   'btn-load-project', 'project-file', 'sim-partners', 'dram-model',
   'dram-size', 'dram-latency', 'dram-hint', 'workload', 'workload-path',
   'sim-flags', 'sim-timeout', 'btn-sim-build', 'btn-sim-run', 'sim-panel',
   'sim-badge', 'sim-phase', 'sim-elapsed', 'sim-log', 'sim-diagnostic',
   'sim-error-reason', 'sim-error-location', 'sim-error-hint', 'sim-zip',
   'btn-sim-hide', 'sim-files',
  ].forEach((id) => { els[id] = $(id); });

  renderThemeOptions();
  try { applyTheme(localStorage.getItem('chipshop-theme') || 'dark'); }
  catch (_) { applyTheme('dark'); }

  els['btn-add-core'].addEventListener('click', addCoreType);
  els['btn-settings'].addEventListener('click', () => {
    els['settings-panel'].hidden = !els['settings-panel'].hidden;
  });
  els['btn-help'].addEventListener('click', () => els['help-dialog'].showModal());
  els['theme'].addEventListener('change', () => applyTheme(els['theme'].value));
  els['btn-save-project'].addEventListener('click', saveProject);
  els['btn-load-project'].addEventListener('click', () => els['project-file'].click());
  els['project-file'].addEventListener('change', () =>
    readProjectFile(els['project-file'].files[0]));

  els['btn-gpu-pick'].addEventListener('click', openGpuDialog);
  els['btn-gpu-clear'].addEventListener('click', () => {
    state.spec.gpu_enabled = false;
    state.spec.gpu_id = null;
    renderGpuSummary();
    hideConfig();
    scheduleEstimate();
  });
  els['gpu-dialog'].addEventListener('close', () => {
    if (els['gpu-dialog'].returnValue === 'apply') applyGpuChoice();
  });

  ['pdk', 'l3', 'l3-ways', 'l3-banks'].forEach((id) => {
    els[id].addEventListener('change', () => {
      state.activePreset = null;
      document.querySelectorAll('.preset').forEach((b) => b.classList.remove('active'));
      readForm();
      updateHints();
      renderGpuSummary();
      hideConfig();
      scheduleEstimate();
    });
  });

  els['l3-enabled'].addEventListener('change', () => {
    syncL3Controls();
    hideConfig();
    scheduleEstimate();
  });

  els['npu-enabled'].addEventListener('change', () => {
    syncNpuControls();
    hideConfig();
    scheduleEstimate();
  });

  els['npu'].addEventListener('change', () => {
    syncNpuControls();
    hideConfig();
    scheduleEstimate();
  });

  els['linux-target'].addEventListener('change', () => {
    els['linux-state'].textContent = els['linux-target'].checked ? 'an' : 'aus';
    hideConfig();
    scheduleEstimate();
  });

  ['dram-model', 'dram-size', 'dram-latency'].forEach((id) => {
    els[id].addEventListener('change', () => {
      syncDramControls();
      hideConfig();
      scheduleEstimate();
    });
  });

  ['rocc', 'activity', 'cycles'].forEach((id) => {
    els[id].addEventListener('change', () => {
      hideConfig();
      scheduleEstimate();
    });
  });

  els['jobs'].addEventListener('change', () => {
    if (state.estimate) renderCards(state.estimate);
  });

  els['workload'].addEventListener('change', () => {
    if (els['workload'].value) els['workload-path'].value = els['workload'].value;
  });

  els['btn-config'].addEventListener('click', () => showConfig(false));
  els['btn-write'].addEventListener('click', () => showConfig(true));
  els['btn-build'].addEventListener('click', startBuild);
  els['btn-cancel-view'].addEventListener('click', () => {
    els['build-panel'].hidden = true;
  });
  els['btn-sim-build'].addEventListener('click', () => startSimulation(false));
  els['btn-sim-run'].addEventListener('click', () => startSimulation(true));
  els['btn-sim-hide'].addEventListener('click', () => {
    els['sim-panel'].hidden = true;
  });

  document.addEventListener('keydown', (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
      e.preventDefault();
      startBuild();
    }
  });
}

// ------------------------------------------------------------------ Start

document.addEventListener('DOMContentLoaded', async () => {
  bind();
  try {
    await loadCatalog();
    await loadWorkloads();
    await runEstimate();
    loadJobs();
  } catch (err) {
    els['chipyard-info'].innerHTML =
      `<span class="pill bad">Katalog nicht ladbar: ${esc(err.message)}</span>`;
    showError('Katalog konnte nicht geladen werden: ' + err.message);
  }
});
