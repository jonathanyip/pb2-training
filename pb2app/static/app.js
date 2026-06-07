'use strict';

// Base path the app is hosted under (e.g. "/pb2-training"), injected by the
// server into index.html. Empty string when hosted at the domain root.
const BASE_PATH = (typeof window !== 'undefined' && window.__BASE_PATH__) || '';
const API = `${BASE_PATH}/api/v1`;
const CANVAS_W = 960;
const CANVAS_H = 540;

const state = {
  activeTab: 'upload',
  videos: { page: 1, size: 24, total: 0, q: '' },
  pollTimer: null,
  training: newLabelState(),
  validation: newLabelState(),
  settings: { values: {}, schema: {}, defaults: {}, dirty: {} },
  models: { items: [], active: null, selected: null },
  explorer: {
    view: 'videos',
    videos: { page: 1, size: 24, total: 0, q: '' },
    video: null,
    frames: { page: 1, size: 24, total: 0 },
    filter: { status: '', has_ball: '' },
  },
};

function newLabelState() {
  return { frame: null, img: null, box: null, originalBox: null, history: [], remaining: 0 };
}

/* ── Helpers ─────────────────────────────────── */
function $(sel) { return document.querySelector(sel); }
function $all(sel) { return Array.from(document.querySelectorAll(sel)); }
function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === 'class') node.className = v;
    else if (k === 'html') node.innerHTML = v;
    else if (k.startsWith('on') && typeof v === 'function') node.addEventListener(k.slice(2), v);
    else if (v !== null && v !== undefined) node.setAttribute(k, v);
  }
  for (const c of children) node.append(c instanceof Node ? c : document.createTextNode(c));
  return node;
}

async function api(path, options = {}) {
  const res = await fetch(`${API}${path}`, options);
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail || detail; } catch (_) { /* ignore */ }
    throw new Error(detail);
  }
  return res.status === 204 ? null : res.json();
}

function toast(message, type = 'info', ms = 2600) {
  const node = el('div', { class: `toast ${type}` }, message);
  $('#toasts').append(node);
  setTimeout(() => { node.style.opacity = '0'; setTimeout(() => node.remove(), 200); }, ms);
}

function fmtDate(iso) {
  if (!iso) return '';
  const d = new Date(iso);
  return d.toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
}

/* ── Tabs ────────────────────────────────────── */
function showTab(name) {
  state.activeTab = name;
  $all('.tab-btn').forEach((b) => b.classList.toggle('active', b.dataset.tab === name));
  $all('.tab').forEach((t) => t.classList.toggle('active', t.id === `tab-${name}`));
  stopPolling();
  document.body.classList.toggle('labeling-active', name === 'train' || name === 'validate');
  if (name === 'upload') { loadVideos(); startPolling(); }
  if (name === 'train') loadNext('training');
  if (name === 'validate') loadNext('validation');
  if (name === 'explorer') showExplorer();
  if (name === 'settings') loadSettingsTab();
}

/* ── Upload tab ──────────────────────────────── */
function setupUpload() {
  $('#sourceToggle').addEventListener('click', (e) => {
    const btn = e.target.closest('.seg-btn');
    if (!btn) return;
    $all('#sourceToggle .seg-btn').forEach((b) => b.classList.toggle('active', b === btn));
    const youtube = btn.dataset.source === 'youtube';
    $('#sourceYoutube').classList.toggle('hidden', !youtube);
    $('#sourceUpload').classList.toggle('hidden', youtube);
  });

  $('#addUrlsBtn').addEventListener('click', addUrls);
  $('#refreshVideosBtn').addEventListener('click', loadVideos);

  let searchTimer = null;
  $('#videoSearch').addEventListener('input', (e) => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => {
      state.videos.q = e.target.value.trim();
      state.videos.page = 1;
      loadVideos();
    }, 250);
  });

  const fileInput = $('#uploadFile');
  const dz = $('#dropzone');
  $('#browseBtn').addEventListener('click', () => fileInput.click());
  dz.addEventListener('click', (e) => { if (e.target.id !== 'browseBtn') fileInput.click(); });
  dz.addEventListener('dragover', (e) => { e.preventDefault(); dz.classList.add('drag'); });
  dz.addEventListener('dragleave', () => dz.classList.remove('drag'));
  dz.addEventListener('drop', (e) => {
    e.preventDefault();
    dz.classList.remove('drag');
    if (e.dataTransfer.files.length) { fileInput.files = e.dataTransfer.files; onFilePicked(); }
  });
  fileInput.addEventListener('change', onFilePicked);
  $('#uploadBtn').addEventListener('click', uploadFile);
}

function onFilePicked() {
  const file = $('#uploadFile').files[0];
  $('#uploadFileName').textContent = file ? file.name : 'No file selected';
  $('#uploadBtn').disabled = !file;
}

async function addUrls() {
  const urls = $('#ytUrls').value.split('\n').map((s) => s.trim()).filter(Boolean);
  if (!urls.length) { toast('Enter at least one URL', 'error'); return; }
  try {
    await api('/videos', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ urls }) });
    $('#ytUrls').value = '';
    toast(`Queued ${urls.length} video${urls.length > 1 ? 's' : ''}`, 'success');
    await loadVideos();
  } catch (err) { toast(err.message, 'error'); }
}

async function uploadFile() {
  const file = $('#uploadFile').files[0];
  if (!file) return;
  const form = new FormData();
  form.append('file', file);
  $('#uploadBtn').disabled = true;
  try {
    await api('/videos/upload', { method: 'POST', body: form });
    toast(`Uploaded ${file.name}`, 'success');
    $('#uploadFile').value = '';
    onFilePicked();
    await loadVideos();
  } catch (err) { toast(err.message, 'error'); $('#uploadBtn').disabled = false; }
}

async function loadVideos() {
  const { page, q } = state.videos;
  const params = new URLSearchParams({ page });
  if (q) params.set('q', q);
  let data;
  try { data = await api(`/videos?${params}`); } catch (err) { toast(err.message, 'error'); return; }
  state.videos.total = data.total;
  state.videos.size = data.size;
  renderVideos(data.items);
  renderPager();
}

function renderVideos(items) {
  const body = $('#videosBody');
  body.innerHTML = '';
  $('#videoCount').textContent = state.videos.total;
  $('#videosEmpty').classList.toggle('hidden', items.length > 0);

  for (const v of items) {
    const pct = Math.round((v.progress || 0) * 100);
    const progClass = v.status === 'failed' ? 'failed' : (v.status === 'ready' ? 'done' : '');
    const qb = v.queue_breakdown || { training: 0, validation: 0 };

    const actions = el('div', { class: 'row-actions' });
    if (v.status === 'failed') {
      actions.append(el('button', { class: 'btn btn-icon', title: 'Retry', onclick: () => retryVideo(v.id) }, '↻'));
    }
    actions.append(el('button', { class: 'btn btn-icon btn-danger', title: 'Delete', onclick: () => deleteVideo(v.id, v.title) }, '🗑'));

    const tr = el('tr', {},
      el('td', { class: 'title', title: v.title }, v.title),
      el('td', {}, v.source_type),
      el('td', {}, String(v.frame_count)),
      el('td', { class: 'queue-chip', html: `<b>${qb.training}</b> train · <b>${qb.validation}</b> val` }),
      el('td', {}, el('span', { class: `badge ${v.status}` }, v.status)),
      el('td', { class: 'col-progress' },
        el('div', { class: `progress ${progClass}`, title: `${pct}%` }, el('span', { style: `width:${pct}%` }))),
      el('td', { class: 'muted' }, fmtDate(v.created_at)),
      el('td', {}, actions),
    );
    if (v.status === 'failed' && v.error) tr.title = v.error;
    body.append(tr);
  }
}

function renderPager() {
  const { page, size, total } = state.videos;
  const pages = Math.max(1, Math.ceil(total / size));
  const pager = $('#videosPager');
  pager.innerHTML = '';
  if (pages <= 1) return;
  pager.append(
    el('button', { class: 'btn btn-icon', disabled: page <= 1 ? '' : null, onclick: () => { state.videos.page--; loadVideos(); } }, '‹'),
    el('span', {}, `Page ${page} / ${pages}`),
    el('button', { class: 'btn btn-icon', disabled: page >= pages ? '' : null, onclick: () => { state.videos.page++; loadVideos(); } }, '›'),
  );
}

async function retryVideo(id) {
  try { await api(`/videos/${id}/retry`, { method: 'POST' }); toast('Retry queued', 'success'); await loadVideos(); }
  catch (err) { toast(err.message, 'error'); }
}

async function deleteVideo(id, title) {
  if (!confirm(`Delete "${title}" and all its frames?`)) return;
  try { await api(`/videos/${id}`, { method: 'DELETE' }); toast('Video deleted', 'success'); await loadVideos(); }
  catch (err) { toast(err.message, 'error'); }
}

function startPolling() {
  stopPolling();
  state.pollTimer = setInterval(() => { if (state.activeTab === 'upload') loadVideos(); }, 2000);
}
function stopPolling() { if (state.pollTimer) { clearInterval(state.pollTimer); state.pollTimer = null; } }

/* ── Labeling (Train / Validate) ─────────────── */
function drawFrame(kind) {
  const canvas = $(`#${kind}Canvas`);
  const ctx = canvas.getContext('2d');
  const s = state[kind];
  ctx.clearRect(0, 0, CANVAS_W, CANVAS_H);
  if (!s.img) { ctx.fillStyle = '#000'; ctx.fillRect(0, 0, CANVAS_W, CANVAS_H); return; }
  ctx.drawImage(s.img, 0, 0, CANVAS_W, CANVAS_H);
  if (s.box) {
    ctx.strokeStyle = '#ff4d4d';
    ctx.lineWidth = 2;
    ctx.strokeRect(s.box.x, s.box.y, s.box.w, s.box.h);
    ctx.fillStyle = 'rgba(255,77,77,0.12)';
    ctx.fillRect(s.box.x, s.box.y, s.box.w, s.box.h);
  }
}

function canvasPos(canvas, evt) {
  const rect = canvas.getBoundingClientRect();
  const scaleX = CANVAS_W / rect.width;
  const scaleY = CANVAS_H / rect.height;
  return {
    x: Math.max(0, Math.min(CANVAS_W, (evt.clientX - rect.left) * scaleX)),
    y: Math.max(0, Math.min(CANVAS_H, (evt.clientY - rect.top) * scaleY)),
  };
}

function wireCanvas(kind) {
  const canvas = $(`#${kind}Canvas`);
  let start = null;
  canvas.addEventListener('mousedown', (e) => {
    if (!state[kind].img) return;
    start = canvasPos(canvas, e);
  });
  canvas.addEventListener('mousemove', (e) => {
    if (!start) return;
    const p = canvasPos(canvas, e);
    state[kind].box = { x: Math.min(start.x, p.x), y: Math.min(start.y, p.y), w: Math.abs(p.x - start.x), h: Math.abs(p.y - start.y) };
    drawFrame(kind);
  });
  const finish = (e) => {
    if (!start) return;
    const p = canvasPos(canvas, e);
    const box = { x: Math.min(start.x, p.x), y: Math.min(start.y, p.y), w: Math.abs(p.x - start.x), h: Math.abs(p.y - start.y) };
    state[kind].box = (box.w >= 4 && box.h >= 4) ? box : null;
    start = null;
    drawFrame(kind);
  };
  canvas.addEventListener('mouseup', finish);
  canvas.addEventListener('mouseleave', finish);
}

function boxFromPrelabel(pre) {
  return {
    x: (pre.x_center - pre.width / 2) * CANVAS_W,
    y: (pre.y_center - pre.height / 2) * CANVAS_H,
    w: pre.width * CANVAS_W,
    h: pre.height * CANVAS_H,
  };
}

async function loadNext(kind) {
  const s = state[kind];
  let data;
  try { data = await api(`/frames/next?queue=${kind}`); } catch (err) { toast(err.message, 'error'); return; }
  s.remaining = data.remaining;
  updateQueueStat(kind);

  const overlay = $(`#${kind}Overlay`);
  if (!data.frame) {
    s.frame = null; s.img = null; s.box = null; s.originalBox = null;
    drawFrame(kind);
    overlay.classList.remove('hidden');
    overlay.innerHTML = kind === 'training'
      ? '<h3>No more training frames.</h3><p class="muted">Head to the Upload tab to add more videos.</p>'
      : '<h3>No more frames to validate.</h3><p class="muted">Add more videos in the Upload tab.</p>';
    $(`#${kind}Meta`).textContent = '';
    return;
  }
  overlay.classList.add('hidden');
  s.frame = data.frame;
  const img = new Image();
  img.onload = () => {
    s.img = img;
    if (data.frame.prelabel) {
      s.originalBox = boxFromPrelabel(data.frame.prelabel);
      s.box = { ...s.originalBox };
    } else {
      s.originalBox = null;
      s.box = null;
    }
    drawFrame(kind);
  };
  img.src = data.frame.image_url;
  const idx = s.history.length + 1;
  $(`#${kind}Meta`).textContent = `${data.frame.video_title} · ${data.frame.id.slice(0, 8)}… · ${idx}/${idx + Math.max(0, data.remaining - 1)}`;
}

function updateQueueStat(kind) {
  $(`#${kind}Remaining`).textContent = state[kind].remaining;
}

function resetBox(kind) {
  const s = state[kind];
  if (!s.frame) return;
  // Training frames have no model guess to reset to, so "reset" means "no ball"
  // (same as clear). Validation resets back to the saved/guessed box.
  s.box = (kind === 'training') ? null : (s.originalBox ? { ...s.originalBox } : null);
  drawFrame(kind);
}

function clearBox(kind) {
  const s = state[kind];
  if (!s.frame) return;
  s.box = null;
  drawFrame(kind);
}

async function undoCurrent(kind) {
  const s = state[kind];
  const prev = s.history.pop();
  if (!prev) { toast('Nothing to undo.'); return; }
  try {
    await api(`/frames/${prev}/reopen`, { method: 'POST' });
    await loadNext(kind);
  } catch (err) { toast(err.message, 'error'); s.history.push(prev); }
}

async function saveNext(kind) {
  const s = state[kind];
  if (!s.frame) return;
  const boxes = [];
  if (s.box && s.box.w >= 4 && s.box.h >= 4) {
    boxes.push({
      class_id: 0,
      x_center: (s.box.x + s.box.w / 2) / CANVAS_W,
      y_center: (s.box.y + s.box.h / 2) / CANVAS_H,
      width: s.box.w / CANVAS_W,
      height: s.box.h / CANVAS_H,
    });
  }
  const savedId = s.frame.id;
  try {
    await api(`/frames/${savedId}/label`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ boxes }),
    });
    s.history.push(savedId);
    await loadNext(kind);
  } catch (err) { toast(err.message, 'error'); }
}

function setupLabeler() {
  wireCanvas('training');
  wireCanvas('validation');
  $all('.hk').forEach((btn) => btn.addEventListener('click', () => {
    const { kind, act } = btn.dataset;
    if (act === 'undo') undoCurrent(kind);
    if (act === 'reset') resetBox(kind);
    if (act === 'clear') clearBox(kind);
    if (act === 'save') saveNext(kind);
  }));
  document.addEventListener('keydown', (e) => {
    const kind = state.activeTab === 'train' ? 'training' : (state.activeTab === 'validate' ? 'validation' : null);
    if (!kind) return;
    if (e.target.matches('input, textarea, select')) return;
    const k = e.key.toLowerCase();
    if (k === 'a') { e.preventDefault(); undoCurrent(kind); }
    if (k === 'w') { e.preventDefault(); resetBox(kind); }
    if (k === 's') { e.preventDefault(); clearBox(kind); }
    if (k === 'd') { e.preventDefault(); saveNext(kind); }
  });
}

/* ── Settings tab ────────────────────────────── */
async function loadSettingsTab() {
  await Promise.all([loadModels(), loadSettings()]);
}

async function loadModels() {
  let data;
  try { data = await api('/models'); } catch (err) { toast(err.message, 'error'); return; }
  state.models.items = data.items;
  state.models.active = data.active ? data.active.id : null;
  state.models.selected = state.models.active;
  renderModels();
}

function renderModels() {
  const wrap = $('#modelsList');
  wrap.innerHTML = '';
  if (!state.models.items.length) { wrap.append(el('p', { class: 'muted' }, 'No models yet.')); return; }
  const byId = Object.fromEntries(state.models.items.map((m) => [m.id, m]));
  for (const m of state.models.items) {
    const lineage = m.is_bootstrap
      ? `seeded (${m.base_weights || 'bootstrap'})`
      : `trained from ${m.base_model_id && byId[m.base_model_id] ? byId[m.base_model_id].name : 'scratch'}`;
    const map = m.metrics && (m.metrics.map50 !== undefined) ? ` · mAP ${m.metrics.map50}` : '';
    const row = el('div', { class: `model-row${m.id === state.models.selected ? ' selected' : ''}`, onclick: () => { state.models.selected = m.id; renderModels(); } },
      el('input', { type: 'radio', name: 'active-model', ...(m.id === state.models.selected ? { checked: '' } : {}) }),
      el('div', {},
        el('div', { class: 'name' }, `${m.name}  v${String(m.version).padStart(4, '0')}`),
        el('div', { class: 'meta' }, `${lineage}${map} · ${m.trained_frames} frames`)),
    );
    if (m.is_active) row.append(el('span', { class: 'badge ready active-tag' }, 'active'));
    wrap.append(row);
  }
}

async function setActiveModel() {
  const id = state.models.selected;
  if (!id) return;
  try { await api(`/models/${id}/activate`, { method: 'POST' }); toast('Active model updated', 'success'); await loadModels(); }
  catch (err) { toast(err.message, 'error'); }
}

async function loadSettings() {
  let data;
  try { data = await api('/settings'); } catch (err) { toast(err.message, 'error'); return; }
  state.settings.values = data.values;
  state.settings.schema = data.schema;
  state.settings.defaults = data.defaults || {};
  state.settings.dirty = {};
  renderSettings();
}

function renderSettings() {
  const form = $('#settingsForm');
  form.innerHTML = '';
  const groups = {};
  for (const [key, meta] of Object.entries(state.settings.schema)) {
    const g = meta.group || 'other';
    (groups[g] = groups[g] || []).push(key);
  }
  for (const group of Object.keys(groups).sort()) {
    const grid = el('div', { class: 'settings-grid' });
    for (const key of groups[group].sort()) {
      grid.append(renderSettingField(key));
    }
    form.append(el('div', { class: 'settings-group' }, el('h4', {}, group), grid));
  }
}

function renderSettingField(key) {
  const meta = state.settings.schema[key];
  const value = state.settings.values[key];
  const label = el('label', { for: `set-${key}` }, key.split('.').slice(1).join('.') || key);
  let input;
  if (meta.type === 'boolean') {
    input = el('input', { id: `set-${key}`, type: 'checkbox', ...(value ? { checked: '' } : {}) });
  } else if (meta.type === 'integer' || meta.type === 'number') {
    input = el('input', { id: `set-${key}`, type: 'number', value: value === null || value === undefined ? '' : value,
      ...(meta.type === 'integer' ? { step: '1' } : { step: 'any' }) });
  } else {
    input = el('input', { id: `set-${key}`, type: 'text', value: value === null || value === undefined ? '' : value });
  }
  const field = el('div', { class: 'setting-field' + (meta.type === 'boolean' ? ' switch-field' : '') }, label,
    meta.type === 'boolean' ? el('div', { class: 'switch' }, input) : input);
  input.addEventListener('input', () => { state.settings.dirty[key] = readField(key, input, meta.type); field.classList.add('dirty'); });
  return field;
}

function readField(key, input, type) {
  if (type === 'boolean') return input.checked;
  if (input.value === '') return null;
  if (type === 'integer') return parseInt(input.value, 10);
  if (type === 'number') return parseFloat(input.value);
  return input.value;
}

async function saveSettings() {
  const values = state.settings.dirty;
  if (!Object.keys(values).length) { toast('No changes to save'); return; }
  try {
    const res = await api('/settings', { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ values }) });
    toast(`Saved ${res.updated.length} setting${res.updated.length > 1 ? 's' : ''}`, 'success');
    await loadSettings();
  } catch (err) { toast(err.message, 'error'); }
}

async function resetSettings() {
  const editable = Object.keys(state.settings.schema).filter((k) => !/^(storage|database|server)\./.test(k));
  const values = {};
  for (const k of editable) if (k in state.settings.defaults) values[k] = state.settings.defaults[k];
  if (!Object.keys(values).length) { toast('No defaults available'); return; }
  if (!confirm('Reset all settings to their built-in defaults?')) return;
  try {
    await api('/settings', { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ values }) });
    toast('Settings reset to defaults', 'success');
    await loadSettings();
  } catch (err) { toast(err.message, 'error'); }
}

/* ── Explorer tab ────────────────────────────── */
function showExplorer() {
  if (state.explorer.view === 'frames' && state.explorer.video) {
    enterFramesView();
  } else {
    enterVideosView();
  }
}

function enterVideosView() {
  state.explorer.view = 'videos';
  $('#explorerVideos').classList.remove('hidden');
  $('#explorerFrames').classList.add('hidden');
  loadExplorerVideos();
}

async function loadExplorerVideos() {
  const { page, q } = state.explorer.videos;
  const params = new URLSearchParams({ page });
  if (q) params.set('q', q);
  let data;
  try { data = await api(`/videos?${params}`); } catch (err) { toast(err.message, 'error'); return; }
  state.explorer.videos.total = data.total;
  state.explorer.videos.size = data.size;
  renderExplorerVideos(data.items);
}

function renderExplorerVideos(items) {
  const body = $('#explorerVideosBody');
  body.innerHTML = '';
  $('#explorerVideoCount').textContent = state.explorer.videos.total;
  $('#explorerVideosEmpty').classList.toggle('hidden', items.length > 0);

  for (const v of items) {
    const qb = v.queue_breakdown || { training: 0, validation: 0 };
    const row = el('tr', { class: 'clickable', onclick: () => openVideoFrames(v) },
      el('td', { class: 'title', title: v.title }, v.title),
      el('td', {}, v.source_type),
      el('td', {}, String(v.frame_count)),
      el('td', { class: 'queue-chip', html: `<b>${qb.training}</b> train · <b>${qb.validation}</b> val` }),
      el('td', {}, el('span', { class: `badge ${v.status}` }, v.status)),
      el('td', { class: 'muted' }, fmtDate(v.created_at)),
    );
    body.append(row);
  }
  renderExplorerVideosPager();
}

function renderExplorerVideosPager() {
  const { page, size, total } = state.explorer.videos;
  const pages = Math.max(1, Math.ceil(total / size));
  const pager = $('#explorerVideosPager');
  pager.innerHTML = '';
  if (pages <= 1) return;
  pager.append(
    el('button', { class: 'btn btn-icon', disabled: page <= 1 ? '' : null, onclick: () => { state.explorer.videos.page--; loadExplorerVideos(); } }, '‹'),
    el('span', {}, `Page ${page} / ${pages}`),
    el('button', { class: 'btn btn-icon', disabled: page >= pages ? '' : null, onclick: () => { state.explorer.videos.page++; loadExplorerVideos(); } }, '›'),
  );
}

function openVideoFrames(v) {
  state.explorer.video = { id: v.id, title: v.title };
  state.explorer.frames.page = 1;
  state.explorer.filter = { status: '', has_ball: '' };
  $all('#explorerFrames .seg .seg-btn').forEach((b) => b.classList.toggle('active', b.dataset.value === ''));
  enterFramesView();
}

function enterFramesView() {
  state.explorer.view = 'frames';
  $('#explorerVideos').classList.add('hidden');
  $('#explorerFrames').classList.remove('hidden');
  $('#explorerFramesTitle').textContent = state.explorer.video.title;
  loadExplorerFrames();
}

async function loadExplorerFrames() {
  const { video, frames, filter } = state.explorer;
  if (!video) return;
  const params = new URLSearchParams({ page: frames.page });
  if (filter.status) params.set('status', filter.status);
  if (filter.has_ball) params.set('has_ball', filter.has_ball);
  let data;
  try { data = await api(`/videos/${video.id}/frames?${params}`); } catch (err) { toast(err.message, 'error'); return; }
  state.explorer.frames.total = data.total;
  state.explorer.frames.size = data.size;
  renderExplorerFrames(data.items);
}

function renderExplorerFrames(items) {
  const grid = $('#explorerGrid');
  grid.innerHTML = '';
  $('#explorerFrameCount').textContent = state.explorer.frames.total;
  $('#explorerFramesEmpty').classList.toggle('hidden', items.length > 0);
  for (const f of items) grid.append(renderFrameCard(f));
  renderExplorerFramesPager();
}

function frameVerdict(f) {
  if (f.status !== 'processed') return { text: 'unreviewed', cls: 'pending' };
  if (f.has_ball) return { text: 'ball', cls: 'ready' };
  return { text: 'no ball', cls: 'queued' };
}

function renderFrameCard(f) {
  const canvas = el('canvas', { class: 'frame-thumb', width: 240, height: 135 });
  const img = new Image();
  img.onload = () => drawThumb(canvas, img, f.labels);
  img.src = f.image_url;

  const verdict = frameVerdict(f);
  const meta = el('div', { class: 'frame-card-meta' },
    el('span', { class: 'muted', title: f.id }, `#${f.frame_index}`),
    el('span', { class: `badge ${verdict.cls}` }, verdict.text),
  );

  const actions = el('div', { class: 'frame-card-actions' },
    el('button', { class: 'btn btn-icon', title: 'Re-open for labeling', onclick: () => explorerReset(f.id) }, '↺'),
    el('button', { class: 'btn btn-icon', title: 'Mark “no ball”', onclick: () => explorerClear(f.id) }, '⦸'),
    el('button', { class: 'btn btn-icon btn-danger', title: 'Delete frame', onclick: () => explorerDelete(f.id, f.frame_index) }, '🗑'),
  );

  return el('div', { class: 'frame-card' }, canvas, meta, actions);
}

function drawThumb(canvas, img, labels) {
  const ctx = canvas.getContext('2d');
  const W = canvas.width;
  const H = canvas.height;
  ctx.clearRect(0, 0, W, H);
  ctx.drawImage(img, 0, 0, W, H);
  for (const l of labels || []) {
    const human = l.source === 'human';
    ctx.strokeStyle = human ? '#4f8cff' : '#f0b429';
    ctx.lineWidth = 2;
    const x = (l.x_center - l.width / 2) * W;
    const y = (l.y_center - l.height / 2) * H;
    ctx.strokeRect(x, y, l.width * W, l.height * H);
  }
}

function renderExplorerFramesPager() {
  const { page, size, total } = state.explorer.frames;
  const pages = Math.max(1, Math.ceil(total / size));
  const pager = $('#explorerFramesPager');
  pager.innerHTML = '';
  if (pages <= 1) return;
  pager.append(
    el('button', { class: 'btn btn-icon', disabled: page <= 1 ? '' : null, onclick: () => { state.explorer.frames.page--; loadExplorerFrames(); } }, '‹'),
    el('span', {}, `Page ${page} / ${pages}`),
    el('button', { class: 'btn btn-icon', disabled: page >= pages ? '' : null, onclick: () => { state.explorer.frames.page++; loadExplorerFrames(); } }, '›'),
  );
}

async function explorerReset(id) {
  try { await api(`/frames/${id}/reopen`, { method: 'POST' }); toast('Frame re-opened for labeling', 'success'); await loadExplorerFrames(); }
  catch (err) { toast(err.message, 'error'); }
}

async function explorerClear(id) {
  try { await api(`/frames/${id}/clear`, { method: 'POST' }); toast('Frame marked “no ball”', 'success'); await loadExplorerFrames(); }
  catch (err) { toast(err.message, 'error'); }
}

async function explorerDelete(id, idx) {
  if (!confirm(`Delete frame #${idx}? It will no longer be used for training.`)) return;
  try { await api(`/frames/${id}`, { method: 'DELETE' }); toast('Frame deleted', 'success'); await loadExplorerFrames(); }
  catch (err) { toast(err.message, 'error'); }
}

function setupExplorer() {
  $('#explorerRefreshBtn').addEventListener('click', loadExplorerVideos);
  $('#explorerFramesRefreshBtn').addEventListener('click', loadExplorerFrames);
  $('#explorerBackBtn').addEventListener('click', enterVideosView);

  let searchTimer = null;
  $('#explorerSearch').addEventListener('input', (e) => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => {
      state.explorer.videos.q = e.target.value.trim();
      state.explorer.videos.page = 1;
      loadExplorerVideos();
    }, 250);
  });

  $('#explorerFrames').addEventListener('click', (e) => {
    const btn = e.target.closest('.seg-btn');
    if (!btn) return;
    const group = btn.closest('.seg');
    const filter = group.dataset.filter;
    group.querySelectorAll('.seg-btn').forEach((b) => b.classList.toggle('active', b === btn));
    state.explorer.filter[filter] = btn.dataset.value;
    state.explorer.frames.page = 1;
    loadExplorerFrames();
  });
}

/* ── Bootstrap ───────────────────────────────── */
function init() {
  $('#tabs').addEventListener('click', (e) => { const b = e.target.closest('.tab-btn'); if (b) showTab(b.dataset.tab); });
  setupUpload();
  setupLabeler();
  setupExplorer();
  $('#setActiveBtn').addEventListener('click', setActiveModel);
  $('#saveSettingsBtn').addEventListener('click', saveSettings);
  $('#resetSettingsBtn').addEventListener('click', resetSettings);
  $('#refreshSettingsBtn').addEventListener('click', loadSettingsTab);
  showTab('upload');
}

document.addEventListener('DOMContentLoaded', init);
