// Chipyard GUI - Frontend
// Bindet templates/index.html an die Flask-API:
//   GET  /api/catalog                 -> Presets, Kerntypen, Module
//   POST /api/estimate                -> Fläche, Leistung, Wafer, Simulation
//   POST /api/config[?write=1]        -> Scala-Config anzeigen/ablegen
//   POST /api/build                   -> Verilog-Build starten
//   GET  /api/build/<id>              -> Status + Log
//   GET  /api/build/<id>/files|zip    -> Artefakte
//   GET  /api/jobs                    -> frühere Läufe

const API = '/api';

const state = {
  catalog: null,
  spec: null,
  estimate: null,
  jobId: null,
  jobTimer: null,
  version: 0,          // entwertet veraltete Antworten
  activePreset: null,
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

// -------------------------------------------------------------- Katalog

async function loadCatalog() {
  const cat = await api('/catalog');
  state.catalog = cat;
  state.spec = { ...cat.defaults, modules: [...cat.defaults.modules] };

  renderChipyardInfo(cat);
  renderPresets();
  renderCoreTypes();
  renderPdks();
  renderL2();
  renderModules();
  fillFormFromSpec();

  els['target-path'].textContent = cat.config_path || '–';
}

function renderChipyardInfo(cat) {
  const cy = cat.chipyard || {};
  const box = els['chipyard-info'];
  const okDot = cy.exists && cy.conda ? 'ok' : 'bad';
  box.innerHTML =
    `<span class="pill ${okDot}"><span class="dot ${okDot}"></span>` +
    `Chipyard <b>${esc(cy.version || '?')}</b></span>` +
    `<span class="pill">Config <b>${esc(cat.config_class || '–')}</b></span>` +
    `<span class="pill ${cy.root ? (cy.exists ? 'ok' : 'bad') : 'bad'}">` +
    `${esc(cy.root || 'Pfad unbekannt')}</span>` +
    (cy.conda ? '' : '<span class="pill bad">Conda-Umgebung fehlt</span>');
  if (!cy.exists || !cy.conda) {
    toast('Chipyard-Checkout oder Conda-Umgebung fehlt – ' +
          'Verilog-Build ist nicht möglich, Schätzung schon.', 'warning', 9000);
  }
}

const PRESETS = [
  { id: 'min', label: 'Minimal', spec: {
      cores: 1, core_type: 'rocket', l2_kib: 128, pdk: 'sky130',
      modules: ['uart', 'gpio', 'dmi'] } },
  { id: 'quad', label: 'Quad-Core', spec: {
      cores: 4, core_type: 'rocket', l2_kib: 512, pdk: 'sky130',
      modules: ['uart', 'gpio', 'spi', 'i2c', 'dmi'] } },
  { id: 'linux', label: 'Linux-fähig', spec: {
      cores: 4, core_type: 'rocket', l2_kib: 1024, pdk: 'sky130',
      modules: ['uart', 'gpio', 'dmi', 'blockdev', 'spiflash'] } },
  { id: 'ml', label: 'ML-Beschleuniger', spec: {
      cores: 4, core_type: 'rocket', l2_kib: 512, pdk: 'sky130',
      modules: ['uart', 'gpio', 'dmi', 'gemmini'] } },
  { id: 'ooo', label: 'OoO 4-Kern', spec: {
      cores: 4, core_type: 'boom_medium', l2_kib: 1024, pdk: 'sky130',
      modules: ['uart', 'gpio', 'dmi'] } },
  { id: 'many', label: '16-Kern Many-Core', spec: {
      cores: 16, core_type: 'rocket', l2_kib: 1024, pdk: 'sky130',
      modules: ['uart', 'gpio', 'dmi'] } },
  { id: 'hpc', label: 'HPC 7 nm', spec: {
      cores: 8, core_type: 'boom_large', l2_kib: 1024, pdk: 'generic7',
      modules: ['uart', 'gpio', 'dmi', 'gemmini'] } },
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
    b.title = 'Vorlage laden: ' + p.label;
    b.addEventListener('click', () => applyPreset(p));
    box.appendChild(b);
  });
}

function applyPreset(p) {
  state.spec = { ...state.spec, ...p.spec, modules: [...p.spec.modules] };
  state.activePreset = p.id;
  document.querySelectorAll('.preset').forEach((b) => {
    b.classList.toggle('active', b.dataset.preset === p.id);
  });
  fillFormFromSpec();
  scheduleEstimate();
}

function renderCoreTypes() {
  const sel = els['core-type'];
  sel.innerHTML = '';
  state.catalog.core_types.forEach((c) => {
    const o = document.createElement('option');
    o.value = c.id;
    o.textContent = c.label;
    o.title = c.uarch || '';
    sel.appendChild(o);
  });
}

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

function renderL2() {
  const sel = els['l2'];
  sel.innerHTML = '';
  state.catalog.l2_choices.forEach((k) => {
    const o = document.createElement('option');
    o.value = String(k);
    o.textContent = k + ' KiB';
    sel.appendChild(o);
  });
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

// ---------------------------------------------------- Formular <-> Spec

function fillFormFromSpec() {
  const s = state.spec;
  els['cores'].value = s.cores;
  els['cores-num'].value = s.cores;
  els['core-type'].value = s.core_type;
  els['pdk'].value = s.pdk;
  els['l2'].value = String(s.l2_kib);
  els['rocc'].value = s.rocc_count;
  els['activity'].value = s.activity;
  els['cycles'].value = s.cycles_millions;

  document.querySelectorAll('#modules input[type=checkbox]').forEach((cb) => {
    cb.checked = s.modules.includes(cb.value);
    cb.closest('.mod-item').classList.toggle('on', cb.checked);
  });

  updateHints();
}

function readForm() {
  const s = state.spec;
  s.cores = clampInt(els['cores'].value, 1, 16, 1);
  s.core_type = els['core-type'].value;
  s.pdk = els['pdk'].value;
  s.l2_kib = parseInt(els['l2'].value, 10) || 512;
  s.rocc_count = clampInt(els['rocc'].value, 0, 8, 0);
  s.activity = Math.min(1, Math.max(0.05, parseFloat(els['activity'].value) || 1));
  s.cycles_millions = Math.max(1, parseFloat(els['cycles'].value) || 50);
  s.build_system = 'verilator';
  return s;
}

function clampInt(v, lo, hi, dflt) {
  const n = parseInt(v, 10);
  if (Number.isNaN(n)) return dflt;
  return Math.min(hi, Math.max(lo, n));
}

function updateHints() {
  const s = readForm();
  const ct = state.catalog.core_types.find((c) => c.id === s.core_type);
  const pdk = state.catalog.presets.find((p) => p.id === s.pdk);

  const tiles = (s.cores === 1) ? '1 Kern-Tile'
    : s.cores + ' Kern-Tiles im Cluster';
  els['cores-hint'].innerHTML = `${tiles}. Mehr Kerne skalieren Fläche, ` +
    `Leistung und Simulationsdauer überproportional.`;

  if (ct) {
    els['core-type-hint'].innerHTML =
      `<b>${esc(ct.uarch)}</b> · L1 ${ct.l1_kib} KiB · Basisfläche ` +
      `${num(ct.area28, 2)} mm² @ 28 nm`;
  }
  if (pdk) {
    els['pdk-hint'].innerHTML =
      `<b>${esc(pdk.kind)}</b> · ${pdk.node_nm} nm · ` +
      `${num(pdk.vdd, 1)} V · f<sub>max</sub> ${num(pdk.fmax_ghz, 2)} GHz`;
  }
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
  memory: { fill: '#0f766e', stroke: '#2dd4bf', label: 'L2 / LLC' },
  uncore: { fill: '#475569', stroke: '#7c8ea8', label: 'Uncore / Busse' },
  accel:  { fill: '#9d174d', stroke: '#f472b6', label: 'Beschleuniger' },
  periph: { fill: '#15803d', stroke: '#4ade80', label: 'Peripherie' },
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
  const coreShort = (state.catalog.core_types.find(c => c.id === spec.core_type) || {}).label || '';

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
    const label = b.kind === 'cores'
      ? `${b.cores} × ${coreShort.replace(/ -- .*/, '')}`
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
  ['cores', 'memory', 'accel', 'uncore', 'periph'].forEach((kind) => {
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
    sub: `Build grob ${num(s.build_minutes_estimate, 0)} min · -j${jobs}`,
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
}

function renderAssumptions(list) {
  els['assumptions'].innerHTML = (list || [])
    .map((x) => `<li>${esc(x)}</li>`).join('');
}

function renderFooter(d) {
  els['foot-est'].innerHTML =
    `<b>${num(d.spec.cores, 0)} × ${esc(d.core.label)}</b> @ ` +
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
  setBadge('running', 'Startet');
  els['build-phase'].textContent = 'Config wird geschrieben …';
  els['build-log'].textContent = '';
  els['file-list'].innerHTML = '';
  els['btn-zip'].hidden = true;

  try {
    const data = await post('/build', {
      ...spec, jobs, chipyard_root: state.catalog.chipyard.root,
    });
    state.jobId = data.job.id;
    toast('Build gestartet: ' + state.jobId, 'info');
    pollJob();
  } catch (err) {
    setBadge('failed', 'Fehler');
    els['build-phase'].textContent = '';
    showError('Build konnte nicht gestartet werden: ' + err.message);
    els['btn-build'].disabled = false;
  }
}

const PHASES = {
  prepare: 'Config wird geschrieben',
  build: 'Scala/FIRRTL-Build läuft',
  collect: 'Verilog wird eingesammelt',
  done: 'abgeschlossen',
};

function setBadge(cls, text) {
  const b = els['build-badge'];
  b.className = 'badge ' + cls;
  b.textContent = text;
}

async function pollJob() {
  clearTimeout(state.jobTimer);
  const id = state.jobId;
  if (!id) return;
  try {
    const data = await api('/build/' + encodeURIComponent(id));
    const job = data.job;
    if (job.id !== state.jobId) return;   // inzwischen anderer Job

    els['build-phase'].textContent = PHASES[job.phase] || job.phase || '';
    els['build-elapsed'].textContent = secs(job.elapsed_s);
    if (job.log_tail) {
      const log = els['build-log'];
      log.textContent = job.log_tail;
      log.scrollTop = log.scrollHeight;
    }

    if (job.status === 'running') {
      setBadge('running', 'Läuft');
      state.jobTimer = setTimeout(pollJob, 1500);
      return;
    }

    els['btn-build'].disabled = false;
    if (job.status === 'done') {
      setBadge('done', 'Fertig');
      await loadArtifacts(id, job);
      toast('Verilog erfolgreich erzeugt', 'success');
      loadJobs();
    } else {
      setBadge('failed', 'Fehlgeschlagen');
      if (job.error) showError(job.error);
      toast('Build fehlgeschlagen', 'error', 6000);
      loadJobs();
    }
  } catch (err) {
    setBadge('failed', 'Fehler');
    showError('Status konnte nicht abgefragt werden: ' + err.message);
    els['btn-build'].disabled = false;
  }
}

async function loadArtifacts(id, job) {
  try {
    const data = await api('/build/' + encodeURIComponent(id) + '/files');
    const files = data.files || [];
    const ul = els['file-list'];
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
      ul.innerHTML = '<li class="muted">Keine Verilog-Dateien gefunden.</li>';
    } else {
      els['btn-zip'].hidden = false;
      els['btn-zip'].href = `${API}/build/${encodeURIComponent(id)}/zip`;
    }
    const rtl = data.rtl_files ?? (job && job.verilog
      ? job.verilog.rtl_files : null);
    els['build-phase'].textContent =
      `${files.length} Dateien` + (rtl ? `, davon ${rtl} RTL` : '') +
      (data.total_bytes ? ` · ${bytes(data.total_bytes)}` : '');
  } catch (err) {
    toast('Dateiliste nicht abrufbar: ' + err.message, 'warning');
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
    jobs.slice(0, 12).forEach((j) => {
      const li = document.createElement('li');
      li.className = 'job-item';
      const cls = j.status === 'done' ? 'done'
        : j.status === 'running' ? 'running' : 'failed';
      const when = j.started
        ? new Date(j.started * 1000).toLocaleString('de-DE')
        : '?';
      li.innerHTML =
        `<span class="badge ${cls}">${esc(j.status)}</span>` +
        `<span class="job-meta">` +
        `<span class="job-spec">${num(j.cores, 0)} × ${esc(j.core_type || '?')} · ` +
        `${esc(j.pdk || '?')}${j.has_verilog ? ' · Verilog ✓' : ''}</span>` +
        `<span class="job-id">${esc(when)} · ${esc(j.id)}</span>` +
        `</span>`;
      li.addEventListener('click', () => openJob(j.id));
      ul.appendChild(li);
    });
  } catch (err) {
    ul.innerHTML = `<li class="muted">Historie nicht ladbar: ${esc(err.message)}</li>`;
  }
}

async function openJob(id) {
  state.jobId = id;
  els['build-panel'].hidden = false;
  els['btn-build'].disabled = false;
  setBadge('running', '…');
  els['build-phase'].textContent = 'Lade Lauf ' + id;
  try {
    const data = await api('/build/' + encodeURIComponent(id));
    const job = data.job;
    els['build-log'].textContent = job.log_tail || '';
    els['build-elapsed'].textContent = secs(job.elapsed_s);
    els['build-phase'].textContent = PHASES[job.phase] || job.phase || '';
    if (job.status === 'done' && job.verilog && job.verilog.found) {
      setBadge('done', 'Fertig');
      await loadArtifacts(id, job);
    } else if (job.status === 'running') {
      setBadge('running', 'Läuft');
      pollJob();
    } else {
      setBadge('failed', 'Fehlgeschlagen');
      if (job.error) showError(job.error);
      els['file-list'].innerHTML = '';
      els['btn-zip'].hidden = true;
    }
  } catch (err) {
    setBadge('failed', 'Fehler');
    showError('Lauf nicht ladbar: ' + err.message);
  }
}

// ----------------------------------------------------------- Verdrahtung

function bind() {
  ['chipyard-info', 'preset-row', 'cores', 'cores-num', 'cores-hint',
   'core-type', 'core-type-hint', 'pdk', 'pdk-hint', 'l2', 'modules',
   'rocc', 'activity', 'cycles', 'jobs', 'floorplan', 'legend',
   'layout-summary', 'cards', 'assumptions', 'target-path', 'btn-config',
   'btn-write', 'btn-build', 'warnings', 'config-source', 'build-panel',
   'build-badge', 'build-phase', 'build-elapsed', 'build-log', 'btn-zip',
   'btn-cancel-view', 'file-list', 'job-list', 'foot-est',
  ].forEach((id) => { els[id] = $(id); });

  const onSpecChange = () => {
    els['cores-num'].value = els['cores'].value;
    statesSync();
    hideConfig();
    scheduleEstimate();
  };
  els['cores'].addEventListener('input', onSpecChange);

  els['cores-num'].addEventListener('change', () => {
    els['cores'].value = clampInt(els['cores-num'].value, 1, 16, 1);
    onSpecChange();
  });

  ['core-type', 'pdk', 'l2'].forEach((id) => {
    els[id].addEventListener('change', () => {
      state.activePreset = null;
      document.querySelectorAll('.preset').forEach((b) => {
        b.classList.remove('active');
      });
      statesSync();
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

  els['btn-config'].addEventListener('click', () => showConfig(false));
  els['btn-write'].addEventListener('click', () => showConfig(true));
  els['btn-build'].addEventListener('click', startBuild);
  els['btn-cancel-view'].addEventListener('click', () => {
    els['build-panel'].hidden = true;
  });

  document.addEventListener('keydown', (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
      e.preventDefault();
      startBuild();
    }
  });
}

function statesSync() {
  readForm();
  updateHints();
}

// ------------------------------------------------------------------ Start

document.addEventListener('DOMContentLoaded', async () => {
  bind();
  try {
    await loadCatalog();
    await runEstimate();
    loadJobs();
  } catch (err) {
    els['chipyard-info'].innerHTML =
      `<span class="pill bad">Katalog nicht ladbar: ${esc(err.message)}</span>`;
    showError('Katalog konnte nicht geladen werden: ' + err.message);
  }
});
