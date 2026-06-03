const API = '/api/v1';
const state = {
  training: { frame: null, img: null, box: null, originalBox: null, history: [] },
  validation: { frame: null, img: null, box: null, originalBox: null, history: [] },
};

function showTab(name) {
  for (const el of document.querySelectorAll('.tab')) el.classList.remove('active');
  document.getElementById(`tab-${name}`).classList.add('active');
  if (name === 'upload') loadVideos();
  if (name === 'train') loadNext('training');
  if (name === 'validate') loadNext('validation');
  if (name === 'settings') loadSettings();
}

async function addUrls() {
  const urls = document.getElementById('ytUrls').value.split('\n').map(s => s.trim()).filter(Boolean);
  await fetch(`${API}/videos`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ urls }) });
  await loadVideos();
}

async function uploadFile() {
  const file = document.getElementById('uploadFile').files[0];
  if (!file) return;
  const form = new FormData();
  form.append('file', file);
  await fetch(`${API}/videos/upload`, { method: 'POST', body: form });
  await loadVideos();
}

async function loadVideos() {
  const res = await fetch(`${API}/videos?page=1`);
  const data = await res.json();
  const tb = document.querySelector('#videosTable tbody');
  tb.innerHTML = '';
  for (const v of data.items) {
    const tr = document.createElement('tr');
    tr.innerHTML = `<td>${v.title}</td><td>${v.source_type}</td><td>${v.frame_count}</td><td>${v.status}</td><td>${Math.round((v.progress || 0) * 100)}%</td>`;
    tb.appendChild(tr);
  }
}

function drawFrame(kind) {
  const ctx = document.getElementById(`${kind}Canvas`).getContext('2d');
  const s = state[kind];
  if (!s.img) return;
  ctx.clearRect(0, 0, 960, 540);
  ctx.drawImage(s.img, 0, 0, 960, 540);
  if (s.box) {
    ctx.strokeStyle = '#ff2d2d';
    ctx.lineWidth = 2;
    ctx.strokeRect(s.box.x, s.box.y, s.box.w, s.box.h);
  }
}

function wireCanvas(kind) {
  const canvas = document.getElementById(`${kind}Canvas`);
  let start = null;
  canvas.addEventListener('mousedown', (e) => {
    const rect = canvas.getBoundingClientRect();
    start = { x: e.clientX - rect.left, y: e.clientY - rect.top };
  });
  canvas.addEventListener('mouseup', (e) => {
    if (!start) return;
    const rect = canvas.getBoundingClientRect();
    const x = e.clientX - rect.left;
    const y = e.clientY - rect.top;
    state[kind].box = { x: Math.min(start.x, x), y: Math.min(start.y, y), w: Math.abs(x - start.x), h: Math.abs(y - start.y) };
    start = null;
    drawFrame(kind);
  });
}

async function loadNext(kind) {
  const res = await fetch(`${API}/frames/next?queue=${kind}`);
  const data = await res.json();
  const s = state[kind];
  if (!data.frame) {
    document.getElementById(`${kind}Meta`).textContent = `No more ${kind} frames.`;
    s.frame = null; s.img = null; s.box = null; drawFrame(kind); return;
  }
  s.frame = data.frame;
  const img = new Image();
  img.onload = () => {
    s.img = img;
    if (kind === 'validation' && data.frame.prelabel) {
      s.originalBox = {
        x: (data.frame.prelabel.x_center - data.frame.prelabel.width / 2) * 960,
        y: (data.frame.prelabel.y_center - data.frame.prelabel.height / 2) * 540,
        w: data.frame.prelabel.width * 960,
        h: data.frame.prelabel.height * 540,
      };
      s.box = { ...s.originalBox };
    } else {
      s.originalBox = null;
      s.box = null;
    }
    drawFrame(kind);
  };
  img.src = data.frame.image_url;
  document.getElementById(`${kind}Meta`).textContent = `${data.frame.video_title} · remaining ${data.remaining}`;
}

function resetBox(kind) {
  const s = state[kind];
  s.box = kind === 'validation' ? (s.originalBox ? { ...s.originalBox } : null) : null;
  drawFrame(kind);
}

async function undoCurrent(kind) {
  const s = state[kind];
  if (!s.frame) return;
  await fetch(`${API}/frames/${s.frame.id}/reopen`, { method: 'POST' });
  await loadNext(kind);
}

async function saveNext(kind) {
  const s = state[kind];
  if (!s.frame) return;
  const boxes = [];
  if (s.box && s.box.w >= 4 && s.box.h >= 4) {
    boxes.push({
      class_id: 0,
      x_center: (s.box.x + s.box.w / 2) / 960,
      y_center: (s.box.y + s.box.h / 2) / 540,
      width: s.box.w / 960,
      height: s.box.h / 540,
    });
  }
  await fetch(`${API}/frames/${s.frame.id}/label`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ boxes }),
  });
  await loadNext(kind);
}

async function loadSettings() {
  const models = await fetch(`${API}/models`).then(r => r.json());
  const settings = await fetch(`${API}/settings`).then(r => r.json());
  const active = models.active ? models.active.id : null;
  const list = models.items.map(m => `<label><input type="radio" name="active-model" value="${m.id}" ${m.id===active?'checked':''}> v${String(m.version).padStart(4,'0')} ${m.name}</label>`).join('<br/>');
  document.getElementById('models').innerHTML = `${list}<br/><button onclick="activateSelectedModel()">Set active</button>`;
  document.getElementById('settingsJson').textContent = JSON.stringify(settings.values, null, 2);
}

async function activateSelectedModel() {
  const selected = document.querySelector('input[name="active-model"]:checked');
  if (!selected) return;
  await fetch(`${API}/models/${selected.value}/activate`, { method: 'POST' });
  await loadSettings();
}

document.addEventListener('keydown', (e) => {
  const trainActive = document.getElementById('tab-train').classList.contains('active');
  const validateActive = document.getElementById('tab-validate').classList.contains('active');
  const kind = trainActive ? 'training' : (validateActive ? 'validation' : null);
  if (!kind) return;
  if (e.key.toLowerCase() === 'a') undoCurrent(kind);
  if (e.key.toLowerCase() === 'w') resetBox(kind);
  if (e.key.toLowerCase() === 'd') saveNext(kind);
});

wireCanvas('training');
wireCanvas('validation');
loadVideos();
