// ============================================================
// АОИ-Web — клиентская часть.
// Чистый JavaScript (ES2020), без внешних зависимостей.
// ============================================================

const TOKEN_KEY = 'aoi_token';
const ROLE_KEY = 'aoi_role';
const USER_KEY = 'aoi_user';

const state = {
  token: localStorage.getItem(TOKEN_KEY) || null,
  role: localStorage.getItem(ROLE_KEY) || null,
  user: localStorage.getItem(USER_KEY) || null,
  defectClasses: [],
  /** @type {Record<string, {kind?: string, label?: string, review_required?: boolean}>} */
  classSemantics: {},
  meta: { live_analysis_interval_ms: 1200, live_analysis_max_side: 640, detection_conf_threshold: 0.25 },
  /** @type {any[]|null} последний список устройств из SSE (для таблицы без лишнего GET) */
  devicesListCache: null,
  /** Операторы для select «Закреплён за» */
  deviceOperatorOptions: null,
  /** EventSource GET /api/devices/registry-events */
  registryEventSource: null,
  resultView: { zoom: 1, pan: { x: 0, y: 0 }, drag: null },
  // Локальная камера (для ручных снимков без регистрации телефона).
  mediaStream: null,
  currentDevice: null,                // взятое этим пользователем устройство
  // Live-анализ поверх локальной камеры.
  live: { enabled: false, timer: null, inflight: false, lastResult: null },
  // Удалённый поток (MJPEG). Live-анализ идёт через отдельные fetch.jpg.
  remote: {
    liveTimer: null,
    liveEnabled: false,
    inflight: false,
    snapshotBlob: null,
    snapshotTimer: null,
    mjpegStarted: false,  // true после первого кадра (SSE) — тогда подключаем GET /stream
    snapshot404Streak: 0,
    /** после 404 frame.jpg не дергаем до нового frame_received_at или до cooldown */
    lastFrameAtFromServer: null,
    snapshotFrameMissing: false,
    snapshotRetryNotBefore: 0,
    status: null,          // снимок с сервера (SSE type:status), те же поля что GET /status
    presetKey: null,
    lastResult: null,      // последний результат live-анализа (для пересчёта overlay)
    eventSource: null,    // SSE /api/devices/{id}/frame-events
    /** id устройства, для которого уже подняты SSE/таймеры (анти-дребезг кликов по вкладке) */
    activeRemoteDeviceId: null,
    /** синхронная защита от двух startRemote до появления EventSource (после await в refreshMyDevice) */
    startRemoteLock: false,
  },
  users: [],
  currentUserId: null,
  /** id инспекций из последнего ответа API журнала (до локального фильтра оператора) */
  historyLastRawIds: [],
  currentInspection: null,
  /** Цифровой зум превью камеры / потока (1 = без увеличения). */
  previewZoom: 1,
  review: {
    inspection: null,
    /** 'defects' | 'recognition' */
    mode: 'defects',
    queueIds: [],
    index: 0,
    verdicts: {}, // defect_id -> { real, excludeTraining } | legacy boolean
    submitting: false,
    zoom: 1.6,
    pan: { x: 0, y: 0 },
    drag: null,
    imageSize: { w: 0, h: 0 },
    feedbackTimer: null,
  },
  /** предпочтение режима списка объектов ('all' | 'recognized' | 'defects'), подтягивается из localStorage */
  inspectDefectsListMode: null,
  /** Редактор эталона Golden Board (вкладка только у администратора) */
  goldenBoard: {
    selectedId: null,
    regions: [],
    drag: null,
    selectedRegionIdx: null,
    /** 'region' | 'marker' — что рисуем на снимке */
    drawMode: 'region',
    markerDrag: null,
  },
};

function isElevatedRole() {
  return state.role === 'manager' || state.role === 'admin';
}

function isAdminRole() {
  return state.role === 'admin';
}

/** Подпись роли для UI (совпадает с серверными значениями `role`). */
function roleRu(role) {
  if (role === 'admin') return 'Администратор';
  if (role === 'manager') return 'Руководитель';
  return 'Сотрудник';
}

function userIsBlocked(u) {
  if (!u.is_active) return true;
  if (u.locked_until) {
    const t = Date.parse(u.locked_until);
    if (!Number.isNaN(t) && t > Date.now()) return true;
  }
  return false;
}

/** Блокировка/разблокировка: admin — все, кроме себя; manager — не admin. */
function canManageUserBlock(u) {
  if (u.id === state.currentUserId) return false;
  if (isAdminRole()) return true;
  if (state.role === 'manager' && u.role !== 'admin') return true;
  return false;
}

let refreshRemoteSyncTimer = null;

function operatorPrefsKey(kind) {
  const u = state.user || localStorage.getItem(USER_KEY) || '';
  return `aoi_op_${kind}_${encodeURIComponent(u)}`;
}

function readOperatorJournalHiddenIds() {
  if (isElevatedRole()) return new Set();
  try {
    const raw = localStorage.getItem(operatorPrefsKey('journal_hidden_ids'));
    if (!raw) return new Set();
    const arr = JSON.parse(raw);
    return new Set(Array.isArray(arr) ? arr.map(Number).filter(Number.isFinite) : []);
  } catch {
    return new Set();
  }
}

function persistOperatorJournalHiddenIds(set) {
  localStorage.setItem(operatorPrefsKey('journal_hidden_ids'), JSON.stringify([...set]));
}

function clearOperatorJournalHiddenIds() {
  localStorage.removeItem(operatorPrefsKey('journal_hidden_ids'));
}

function persistInspectPanelHidden(hidden) {
  const k = operatorPrefsKey('inspect_panel_hidden');
  if (hidden) localStorage.setItem(k, '1');
  else localStorage.removeItem(k);
}

function isInspectPanelHiddenPersisted() {
  return localStorage.getItem(operatorPrefsKey('inspect_panel_hidden')) === '1';
}

/** Только DOM карточки результата, без статусной строки и localStorage. */
function teardownInspectResultDomOnly() {
  state.currentInspection = null;
  const ir = $('#inspect-result');
  if (ir) ir.hidden = true;
  const img = $('#result-image');
  if (img?.src?.startsWith('blob:')) {
    try {
      URL.revokeObjectURL(img.src);
    } catch (_) {
      /* ignore */
    }
  }
  if (img) img.removeAttribute('src');
  const cv = $('#result-defects-overlay');
  if (cv) {
    const ctx = cv.getContext('2d');
    if (ctx && cv.width > 0) ctx.clearRect(0, 0, cv.width, cv.height);
    cv.removeAttribute('width');
    cv.removeAttribute('height');
    cv.style.width = '';
    cv.style.height = '';
  }
  const list = $('#defects-list');
  if (list) list.innerHTML = '';
  const rv = $('#review-status');
  if (rv) rv.textContent = '';
  const btnRev = $('#btn-review');
  if (btnRev) btnRev.hidden = true;
  const train = $('#btn-training-zip');
  if (train) train.hidden = true;
  resetResultView();
}

function revealInspectPanelForOperator() {
  if (!isElevatedRole()) persistInspectPanelHidden(false);
}

function updateHistoryUnhideButton() {
  const btn = $('#btn-history-unhide-local');
  if (!btn) return;
  const n = isElevatedRole() ? 0 : readOperatorJournalHiddenIds().size;
  btn.hidden = isElevatedRole() || n === 0;
}

// ---------- HTTP ----------
async function api(path, { method = 'GET', body, headers = {}, raw = false } = {}) {
  const opts = { method, headers: { ...headers } };
  if (state.token) opts.headers['Authorization'] = `Bearer ${state.token}`;
  if (body instanceof FormData) {
    opts.body = body;
  } else if (body !== undefined) {
    opts.headers['Content-Type'] = 'application/json';
    opts.body = JSON.stringify(body);
  }
  const res = await fetch(path, opts);
  if (res.status === 401 && path === '/api/auth/me') {
    logout();
    throw new Error('Требуется авторизация');
  }
  if (raw) return res;
  if (res.status === 204 || res.status === 205) {
    if (!res.ok) throw new Error(`Ошибка ${res.status}`);
    return null;
  }
  const text = await res.text();
  if (!text) {
    if (!res.ok) throw new Error(`Ошибка ${res.status}`);
    return null;
  }
  const data = res.headers.get('content-type')?.includes('application/json')
    ? JSON.parse(text)
    : text;
  if (!res.ok) {
    const msg = typeof data === 'object' && data?.detail ? data.detail : `Ошибка ${res.status}`;
    throw new Error(Array.isArray(msg) ? msg.map((m) => m.msg || m).join('; ') : msg);
  }
  return data;
}

// ---------- Аутентификация ----------
async function login(username, password) {
  const form = new URLSearchParams();
  form.append('username', username);
  form.append('password', password);
  const res = await fetch('/api/auth/login', {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body: form,
  });
  const data = await res.json();
  if (!res.ok) throw new Error(data.detail || 'Ошибка входа');
  state.token = data.access_token;
  state.role = data.role;
  state.user = data.username;
  localStorage.setItem(TOKEN_KEY, data.access_token);
  localStorage.setItem(ROLE_KEY, data.role);
  localStorage.setItem(USER_KEY, data.username);
  state.inspectDefectsListMode = null;
}

function logout() {
  state.token = null;
  state.role = null;
  state.user = null;
  localStorage.removeItem(TOKEN_KEY);
  localStorage.removeItem(ROLE_KEY);
  localStorage.removeItem(USER_KEY);
  stopLive();
  stopCamera();
  stopRemote();
  closeRegistryEventSource();
  state.devicesListCache = null;
  state.deviceOperatorOptions = null;
  state.inspectDefectsListMode = null;
  showScreen('login');
}

// ---------- UI helpers ----------
function $(sel, root = document) { return root.querySelector(sel); }
function $$(sel, root = document) { return [...root.querySelectorAll(sel)]; }

function showScreen(name) {
  $$('.screen').forEach((el) => el.classList.remove('is-active'));
  $(`#screen-${name}`).classList.add('is-active');
}

function showTab(id) {
  $$('.tab').forEach((t) => t.classList.toggle('is-active', t.dataset.tab === id));
  $$('.tab-panel').forEach((p) => p.classList.toggle('is-active', p.id === id));
}

function applyRoleVisibility() {
  document.body.classList.toggle('role-manager', isElevatedRole());
  document.body.classList.toggle('role-admin', isAdminRole());
}

function formatDate(iso) {
  if (!iso) return '—';
  const d = new Date(iso);
  const pad = (n) => String(n).padStart(2, '0');
  return `${pad(d.getDate())}.${pad(d.getMonth() + 1)}.${d.getFullYear()} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

function formatBytes(n) {
  if (!n) return '—';
  const units = ['Б', 'КБ', 'МБ', 'ГБ'];
  let v = n, i = 0;
  while (v >= 1024 && i < units.length - 1) { v /= 1024; i += 1; }
  return `${v.toFixed(v >= 10 ? 0 : 1)} ${units[i]}`;
}

/** Правило семантики для кода класса (совпадение без учёта регистра). */
function semanticsForCode(classCode) {
  const map = state.classSemantics || {};
  if (classCode == null || classCode === '') return null;
  if (map[classCode]) return map[classCode];
  const low = String(classCode).toLowerCase();
  for (const [k, v] of Object.entries(map)) {
    if (String(k).toLowerCase() === low) return v;
  }
  return null;
}

/** Нужна ли ручная оценка по семантике класса (из /api/meta). */
function defectNeedsReview(classCode) {
  const m = semanticsForCode(classCode);
  if (!m || typeof m !== 'object') return true;
  const k = m.kind || 'defect';
  if (k === 'ignore') return false;
  if (k === 'component') return !!m.review_required;
  return m.review_required !== false;
}

function defectsPendingManualReview(defects) {
  return (defects || []).filter((d) => defectNeedsReview(d.class_code));
}

function isSemanticDefect(d) {
  return (d.semantic_kind || 'defect') === 'defect';
}

/** Очередь «Оценить дефекты» — только семантические дефекты (в т.ч. golden_component_*). */
function defectsForReviewQueue(defects) {
  return (defects || []).filter((d) => isSemanticDefect(d) && defectNeedsReview(d.class_code));
}

/** Очередь «Проверить распознавание» — компоненты с флагом ручной проверки. */
function recognitionForReviewQueue(defects) {
  return (defects || []).filter((d) => {
    const sk = d.semantic_kind || 'defect';
    if (sk === 'ignore' || isSemanticDefect(d)) return false;
    return defectNeedsReview(d.class_code);
  });
}

function filterDefectsForOverlay(defects, mode) {
  if (!defects?.length || mode === 'all') return [];
  return filterDefectsForListMode(defects, mode);
}

function filterDefectsForListMode(defects, mode) {
  if (!defects?.length) return [];
  if (mode === 'all') return [];
  if (mode === 'recognized') {
    return defects.filter((d) => (d.semantic_kind || 'defect') !== 'ignore');
  }
  if (mode === 'defects') {
    return defects.filter((d) => {
      const sk = d.semantic_kind || 'defect';
      if (sk !== 'defect') return false;
      if (!d.is_reviewed) return true;
      return !!d.is_real_defect;
    });
  }
  return [...defects];
}

function semanticKindRu(kind) {
  if (kind === 'component') return 'компонент';
  if (kind === 'ignore') return 'игнор';
  return 'дефект';
}

const INSPECT_DEFECTS_LIST_MODES = ['all', 'recognized', 'defects'];

function readInspectDefectsListMode() {
  try {
    const v = localStorage.getItem(operatorPrefsKey('inspect_defects_list_mode'));
    if (INSPECT_DEFECTS_LIST_MODES.includes(v)) return v;
  } catch {
    /* ignore */
  }
  return 'all';
}

function persistInspectDefectsListMode(mode) {
  try {
    localStorage.setItem(operatorPrefsKey('inspect_defects_list_mode'), mode);
  } catch {
    /* ignore */
  }
}

function getInspectDefectsListMode() {
  if (state.inspectDefectsListMode == null) state.inspectDefectsListMode = readInspectDefectsListMode();
  return state.inspectDefectsListMode;
}

function setInspectDefectsListMode(mode) {
  if (!INSPECT_DEFECTS_LIST_MODES.includes(mode)) return;
  state.inspectDefectsListMode = mode;
  persistInspectDefectsListMode(mode);
}

function syncInspectDefectsFilterToolbar() {
  const mode = getInspectDefectsListMode();
  $$('[data-defects-filter]').forEach((btn) => {
    btn.classList.toggle('is-active', btn.dataset.defectsFilter === mode);
  });
}

/** После проверки: перечёркивание только для ЛП дефекта или ошибки класса; «не брак» по компоненту — без зачёркивания. */
function defectRowRejectionKind(d) {
  const sk = d.semantic_kind || 'defect';
  if (!d.is_reviewed || d.is_real_defect) return null;
  if (d.exclude_from_training) return 'misclassified';
  if (sk === 'defect') return 'false_positive';
  return 'clean_board';
}

function hexToRgba(hex, alpha) {
  const h = String(hex || '').replace('#', '');
  if (h.length !== 6) return `rgba(34, 197, 94, ${alpha})`;
  const r = parseInt(h.slice(0, 2), 16);
  const g = parseInt(h.slice(2, 4), 16);
  const b = parseInt(h.slice(4, 6), 16);
  return `rgba(${r}, ${g}, ${b}, ${alpha})`;
}

function redrawResultDefectOverlay() {
  const data = state.currentInspection;
  const img = $('#result-image');
  const cv = $('#result-defects-overlay');
  if (!data || !img || !cv || !img.complete || !img.naturalWidth) return;
  const ow = img.naturalWidth;
  const oh = img.naturalHeight;
  const cw = img.clientWidth;
  const ch = img.clientHeight;
  if (cw < 2 || ch < 2) return;

  const scale = Math.min(cw / ow, ch / oh);
  const offX = (cw - ow * scale) / 2;
  const offY = (ch - oh * scale) / 2;

  const dpr = window.devicePixelRatio || 1;
  cv.width = Math.round(cw * dpr);
  cv.height = Math.round(ch * dpr);
  cv.style.width = `${cw}px`;
  cv.style.height = `${ch}px`;

  const ctx = cv.getContext('2d');
  if (!ctx) return;
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, cw, ch);

  const mode = getInspectDefectsListMode();
  const defs = filterDefectsForOverlay(data.defects || [], mode);
  const colorMap = Object.fromEntries(state.defectClasses.map((c) => [c.code, c.color]));
  const line = Math.max(2, Math.round(cw / 400));
  const fontPx = Math.max(12, Math.round(cw / 60));
  ctx.font = `${fontPx}px system-ui, sans-serif`;
  ctx.textBaseline = 'top';

  for (const d of defs) {
    const hex = colorMap[d.class_code] || '#22c55e';
    const x1 = offX + d.bbox_x1 * scale;
    const y1 = offY + d.bbox_y1 * scale;
    const x2 = offX + d.bbox_x2 * scale;
    const y2 = offY + d.bbox_y2 * scale;
    const bw = x2 - x1;
    const bh = y2 - y1;
    ctx.strokeStyle = hex;
    ctx.lineWidth = line;
    // Контур сегментации (полигон) рисуем «пиксель-в-пиксель»; иначе — рамку bbox.
    if (Array.isArray(d.polygon) && d.polygon.length >= 3) {
      ctx.beginPath();
      ctx.moveTo(offX + d.polygon[0][0] * scale, offY + d.polygon[0][1] * scale);
      for (let i = 1; i < d.polygon.length; i++) {
        ctx.lineTo(offX + d.polygon[i][0] * scale, offY + d.polygon[i][1] * scale);
      }
      ctx.closePath();
      ctx.fillStyle = hexToRgba(hex, 0.22);
      ctx.fill();
      ctx.stroke();
    } else {
      ctx.strokeRect(x1, y1, bw, bh);
    }
    const label = `${d.class_code} ${Number(d.confidence).toFixed(2)}`;
    const padX = 4;
    const padY = 3;
    const tw = ctx.measureText(label).width;
    const lh = fontPx + padY * 2;
    const ly1 = Math.max(0, y1 - lh);
    ctx.fillStyle = hex;
    ctx.fillRect(x1, ly1, tw + padX * 2, lh);
    ctx.fillStyle = '#000';
    ctx.fillText(label, x1 + padX, ly1 + padY);
  }
}

function renderDefectsList(data) {
  const list = $('#defects-list');
  if (!list) return;
  list.innerHTML = '';
  const raw = data.defects || [];
  const mode = getInspectDefectsListMode();
  syncInspectDefectsFilterToolbar();

  if (!raw.length) {
    list.innerHTML =
      '<div class="defect-row"><span class="defect-dot" style="background:#10b981"></span><span>Объектов не обнаружено</span><span></span><span></span></div>';
    return;
  }

  if (mode === 'all') {
    list.innerHTML =
      '<div class="defect-row defect-row--empty-filter"><span class="defect-dot" style="background:#9ca3af"></span><span>Режим «Полный кадр» — только фото. Переключитесь на «Распознанное» или «Только дефекты».</span><span></span><span></span></div>';
    return;
  }

  const filtered = filterDefectsForListMode(raw, mode);
  if (!filtered.length) {
    list.innerHTML =
      '<div class="defect-row defect-row--empty-filter"><span class="defect-dot" style="background:#9ca3af"></span><span>В этом режиме объектов нет.</span><span></span><span></span></div>';
    return;
  }

  const colorMap = Object.fromEntries(state.defectClasses.map((c) => [c.code, c.color]));
  filtered.forEach((d, idx) => {
    const row = document.createElement('div');
    row.className = 'defect-row';
    let badge = '<span class="defect-badge defect-badge--pending">не проверен</span>';
    if (d.is_reviewed) {
      if (d.is_real_defect) {
        badge = '<span class="defect-badge defect-badge--accepted">подтверждён</span>';
      } else if (d.exclude_from_training) {
        badge = '<span class="defect-badge defect-badge--warn">ошибка распознавания</span>';
      } else {
        badge = '<span class="defect-badge defect-badge--rejected">не дефект</span>';
      }
    }
    const rk = defectRowRejectionKind(d);
    if (rk === 'misclassified' || rk === 'false_positive') row.classList.add('is-rejected');
    else if (rk === 'clean_board') row.classList.add('is-dismissed');

    const sk = d.semantic_kind || 'defect';
    const kindTag = `<span style="opacity:.85;font-size:11px;margin-right:6px">[${semanticKindRu(sk)}]</span>`;
    row.innerHTML = `
      <span class="defect-dot" style="background:${colorMap[d.class_code] || '#6b7280'}"></span>
      <span>${kindTag}<b>${idx + 1}. ${d.class_name}</b> (${d.class_code}) · bbox: ${d.bbox_x1}, ${d.bbox_y1}, ${d.bbox_x2}, ${d.bbox_y2}</span>
      <span>${d.confidence.toFixed(2)}</span>
      ${badge}`;
    list.appendChild(row);
  });
}

function liveSemanticCounts(res) {
  const dc = res.detections_count != null ? res.detections_count : (res.defects?.length ?? 0);
  let sd = res.semantic_defect_count;
  if (sd == null && res.defects?.length) {
    sd = res.defects.filter((x) => (x.semantic_kind || 'defect') === 'defect').length;
  }
  if (sd == null) sd = 0;
  return { sd, dc };
}

function updateDeviceBadge() {
  const badge = $('#device-badge');
  const warn = $('#no-device-warn');
  if (state.currentDevice) {
    badge.textContent = `Устройство: ${state.currentDevice.name}`;
    badge.hidden = false;
    if (warn) warn.hidden = true;
  } else {
    badge.hidden = true;
    if (warn) warn.hidden = false;
  }
  // Не вызываем syncRemoteView() здесь: вызов только при смене «моего» устройства (SSE / после действий).
}

// ---------- Экран входа ----------
$('#login-form').addEventListener('submit', async (ev) => {
  ev.preventDefault();
  const fd = new FormData(ev.target);
  const err = $('#login-error');
  err.textContent = '';
  try {
    await login(fd.get('username'), fd.get('password'));
    await bootApp();
  } catch (e) {
    err.textContent = e.message;
  }
});

$('#btn-logout').addEventListener('click', async () => {
  try { await api('/api/auth/logout', { method: 'POST' }); } catch {}
  logout();
});

// ---------- Переключение табов ----------
$$('.tab').forEach((tab) => {
  tab.addEventListener('click', () => {
    const prevTab = document.querySelector('.tab.is-active')?.dataset.tab;
    showTab(tab.dataset.tab);
    const id = tab.dataset.tab;
    if (id === 'tab-history') loadHistory();
    if (id === 'tab-stats') loadStats();
    if (id === 'tab-users') loadUsers();
    if (id === 'tab-devices') {
      if (state.devicesListCache !== null) renderDevicesTable(state.devicesListCache);
      else loadDevices();
    }
    if (id === 'tab-datasets') loadDatasets();
    if (id === 'tab-settings') loadSettings();
    if (id === 'tab-audit') loadAuditTab();
    if (id === 'tab-golden-boards') loadGoldenBoardsTab();
    // Повторный клик по «Инспекция» не должен рвать SSE/MJPEG.
    if (id === 'tab-inspect' && prevTab !== 'tab-inspect') {
      syncRemoteView();
      loadEsp32HardwareConfig();
      refreshEsp32HardwareStatus(false);
      if (isAdminRole()) refreshWledAdminDiagnostics();
    }
  });
});

// ---------- Источник изображения ----------
$$('.chip[data-source]').forEach((chip) => {
  chip.addEventListener('click', () => {
    const prevChip = document.querySelector('.chip[data-source].is-active');
    const previousSource = prevChip?.dataset.source;
    $$('.chip[data-source]').forEach((c) => c.classList.toggle('is-active', c === chip));
    $$('.source-pane').forEach((p) => p.classList.remove('is-active'));
    $(`#source-${chip.dataset.source}`).classList.add('is-active');
    if (chip.dataset.source !== 'camera') { stopLive(); stopCamera(); }
    if (chip.dataset.source !== 'remote') { stopRemote(); }
    // Повторный клик по «Удалённо» не рвёт EventSource.
    if (chip.dataset.source === 'remote' && previousSource !== 'remote') syncRemoteView();
  });
});

// ---------- Локальная камера ----------
async function startCamera() {
  try {
    state.mediaStream = await navigator.mediaDevices.getUserMedia({
      video: { facingMode: { ideal: 'environment' }, width: { ideal: 1920 }, height: { ideal: 1080 } },
      audio: false,
    });
    const video = $('#video');
    video.srcObject = state.mediaStream;
    await video.play();
    $('#btn-capture').disabled = false;
    $('#btn-camera-start').textContent = 'Остановить камеру';
    setStatus('');
  } catch (e) {
    setStatus(`Не удалось получить доступ к камере: ${e.message}`, 'error');
  }
}

function stopCamera() {
  if (state.mediaStream) {
    state.mediaStream.getTracks().forEach((t) => t.stop());
    state.mediaStream = null;
  }
  const v = $('#video'); if (v) v.srcObject = null;
  const bc = $('#btn-capture'); if (bc) bc.disabled = true;
  const btn = $('#btn-camera-start');
  if (btn) btn.textContent = 'Включить камеру';
  clearOverlay('#overlay');
}

$('#btn-camera-start').addEventListener('click', () => {
  if (state.mediaStream) { stopLive(); stopCamera(); } else startCamera();
});

$('#btn-capture').addEventListener('click', async () => {
  if (!state.currentDevice) {
    setStatus('Сначала возьмите устройство в работу на вкладке «Устройства».', 'warn');
    return;
  }
  const video = $('#video');
  if (!video.videoWidth) return;
  const canvas = $('#canvas');
  canvas.width = video.videoWidth;
  canvas.height = video.videoHeight;
  const ctx = canvas.getContext('2d');
  ctx.drawImage(video, 0, 0);
  const blob = await new Promise((resolve) => canvas.toBlob(resolve, 'image/jpeg', 0.92));
  await submitImage(blob, `capture_${Date.now()}.jpg`);
});

// ---------- Live-анализ (локальная камера) ----------
$('#chk-live').addEventListener('change', (ev) => {
  if (ev.target.checked) startLive(); else stopLive();
});

async function startLive() {
  if (!state.mediaStream) {
    await startCamera();
    if (!state.mediaStream) { $('#chk-live').checked = false; return; }
  }
  state.live.enabled = true;
  scheduleNextLiveFrame(0);
}

function stopLive() {
  state.live.enabled = false;
  state.live.lastResult = null;
  if (state.live.timer) { clearTimeout(state.live.timer); state.live.timer = null; }
  clearOverlay('#overlay');
  const st = $('#live-stats'); if (st) st.textContent = '—';
  const chk = $('#chk-live'); if (chk) chk.checked = false;
}

function scheduleNextLiveFrame(delay) {
  if (!state.live.enabled) return;
  state.live.timer = setTimeout(runLiveFrame, delay);
}

async function runLiveFrame() {
  if (!state.live.enabled) return;
  if (state.live.inflight) { scheduleNextLiveFrame(120); return; }
  const video = $('#video');
  if (!video.videoWidth) { scheduleNextLiveFrame(250); return; }
  state.live.inflight = true;
  try {
    const blob = await downscaleFromVideo(video);
    const fd = new FormData();
    fd.append('image', blob, 'live.jpg');
    appendGoldenBoardProfileId(fd);
    const t0 = performance.now();
    const res = await api('/api/inspections/live', { method: 'POST', body: fd });
    const rtt = (performance.now() - t0).toFixed(0);
    state.live.lastResult = res;
    drawOverlay('#video', '#overlay', res);
    const { sd, dc } = liveSemanticCounts(res);
    $('#live-stats').textContent = `${sd} деф. / ${dc} объекта · ${res.inference_time_ms.toFixed(0)} мс · RTT ${rtt} мс · ${res.backend}`;
  } catch (e) {
    state.live.lastResult = null;
    $('#live-stats').textContent = `Ошибка: ${e.message}`;
  } finally {
    state.live.inflight = false;
    const interval = Math.max(300, state.meta.live_analysis_interval_ms || 1200);
    scheduleNextLiveFrame(interval);
  }
}

async function downscaleFromVideo(video) {
  const maxSide = state.meta.live_analysis_max_side || 640;
  const sw = video.videoWidth, sh = video.videoHeight;
  const scale = Math.min(1, maxSide / Math.max(sw, sh));
  const w = Math.round(sw * scale), h = Math.round(sh * scale);
  const cv = $('#canvas');
  cv.width = w; cv.height = h;
  cv.getContext('2d').drawImage(video, 0, 0, w, h);
  return new Promise((resolve) => cv.toBlob(resolve, 'image/jpeg', 0.7));
}

// ---------- Удалённый поток (MJPEG-просмотр кадров с телефона) ----------
// Статус и кадры приходят по SSE /frame-events (без опроса GET /status).
// Список устройств и «моё» — по SSE /registry-events (без периодического GET /mine).
const REMOTE_SNAPSHOT_MS = 700;
const REMOTE_SNAPSHOT_BACKOFF_MAX = 8000;

function hasRemoteServerFrame() {
  const s = state.remote.status;
  if (!s) return false;
  const can =
    s.frame_available !== undefined
      ? !!s.frame_available
      : !!s.frame_received_at;
  if (!can) return false;
  if (state.remote.snapshotFrameMissing && Date.now() < state.remote.snapshotRetryNotBefore) {
    return false;
  }
  return true;
}

function clearRemotePollingTimers() {
  if (state.remote.snapshotTimer) {
    clearTimeout(state.remote.snapshotTimer);
    state.remote.snapshotTimer = null;
  }
}

/** Данные как у GET /status, пришедшие по SSE (type: status). */
function applyRemoteStatusPayload(s) {
  if (!$('#source-remote')?.classList.contains('is-active')) return;
  const prevAt = state.remote.lastFrameAtFromServer;
  const at = s.frame_received_at ?? null;
  if (at !== prevAt) {
    state.remote.lastFrameAtFromServer = at;
    state.remote.snapshotFrameMissing = false;
    state.remote.snapshot404Streak = 0;
    state.remote.snapshotRetryNotBefore = 0;
    if (at) scheduleRemoteSnapshotTick(0);
  }
  state.remote.status = s;
  if (s.preset) state.remote.presetKey = s.preset;
  updateRemoteControlsUI();
  if (hasRemoteServerFrame()) {
    ensureMjpegStream();
    if (!state.remote.snapshotTimer && state.remote.snapshotBlob === null) {
      scheduleRemoteSnapshotTick(0);
    }
  }
}

function clearSnapshotOnlyTimer() {
  if (state.remote.snapshotTimer) {
    clearTimeout(state.remote.snapshotTimer);
    state.remote.snapshotTimer = null;
  }
}

function scheduleRemoteSnapshotTick(delay) {
  clearSnapshotOnlyTimer();
  state.remote.snapshotTimer = setTimeout(remoteSnapshotTick, delay);
}

async function remoteSnapshotTick() {
  state.remote.snapshotTimer = null;
  const pane = $('#source-remote');
  if (!pane?.classList.contains('is-active') || !state.currentDevice || !state.token) return;

  if (!hasRemoteServerFrame()) return;

  const dev = state.currentDevice;
  try {
    const res = await api(`/api/devices/${dev.id}/frame.jpg`, { raw: true });
    if (res.ok) {
      state.remote.snapshotBlob = await res.blob();
      state.remote.snapshot404Streak = 0;
      state.remote.snapshotFrameMissing = false;
      state.remote.snapshotRetryNotBefore = 0;
      state.remote.snapshotTimer = setTimeout(remoteSnapshotTick, REMOTE_SNAPSHOT_MS);
      return;
    }
    if (res.status === 404) {
      state.remote.snapshot404Streak += 1;
      state.remote.snapshotFrameMissing = true;
      const pause = Math.min(
        REMOTE_SNAPSHOT_BACKOFF_MAX,
        1200 + state.remote.snapshot404Streak * 900,
      );
      state.remote.snapshotRetryNotBefore = Date.now() + pause;
      clearSnapshotOnlyTimer();
      return;
    }
    state.remote.snapshotTimer = setTimeout(remoteSnapshotTick, REMOTE_SNAPSHOT_MS * 2);
  } catch {
    state.remote.snapshotTimer = setTimeout(remoteSnapshotTick, 2000);
  }
}

function syncRemoteView() {
  const pane = $('#source-remote');
  if (!pane || !pane.classList.contains('is-active')) { stopRemote(); return; }

  const empty = $('#remote-empty');
  const view = $('#remote-view');
  const dev = state.currentDevice;
  if (!dev) {
    view.hidden = true;
    empty.hidden = false;
    empty.textContent = 'Возьмите устройство-камеру на вкладке «Устройства». Его видеопоток появится здесь.';
    stopRemote();
    return;
  }
  // Плеер и MJPEG включаем после сигнала с сервера (SSE: кадр доступен).
  view.hidden = false;
  empty.hidden = true;
  startRemote();
}

/** Долгий GET /stream только после того, как сервер сообщил о кадре (SSE или редкий /status). */
function ensureMjpegStream() {
  const dev = state.currentDevice;
  if (!dev || !state.token || state.remote.mjpegStarted) return;
  const pane = $('#source-remote');
  if (!pane?.classList.contains('is-active')) return;
  const img = $('#remote-img');
  if (!img) return;
  const url = `/api/devices/${dev.id}/stream?token=${encodeURIComponent(state.token)}`;
  img.src = url;
  img.dataset.deviceId = String(dev.id);
  img.addEventListener('load', syncRemoteAspect, { once: false });
  state.remote.mjpegStarted = true;
  const stats = $('#remote-stats');
  if (stats && !state.remote.liveEnabled) stats.textContent = 'Поток: MJPEG';
}

function startRemote() {
  const dev = state.currentDevice;
  if (!dev || !state.token) return;
  const pane = $('#source-remote');
  const existing = state.remote.eventSource;
  // CONNECTING (0) / OPEN (1): уже строим или жив одно SSE — не сбрасывать img и не плодить запросы.
  if (
    existing
    && state.remote.activeRemoteDeviceId === dev.id
    && pane?.classList.contains('is-active')
  ) {
    const ok = typeof EventSource !== 'undefined' && (existing.readyState === 0 || existing.readyState === 1);
    if (ok) return;
  }
  if (state.remote.startRemoteLock) return;
  state.remote.startRemoteLock = true;
  try {
    const img = $('#remote-img');
    state.remote.activeRemoteDeviceId = dev.id;
    // MJPEG не открываем сразу: ждём событие по SSE (или кадр из редкого /status без EventSource).
    state.remote.mjpegStarted = false;
    img.src = '';
    img.removeAttribute('data-device-id');

    clearRemotePollingTimers();
    state.remote.snapshot404Streak = 0;
    state.remote.lastFrameAtFromServer = null;
    state.remote.snapshotFrameMissing = false;
    state.remote.snapshotRetryNotBefore = 0;

  startFrameEventSource();

    const ctrl = $('#remote-controls');
    if (ctrl) ctrl.hidden = false;
    updateRemoteControlsUI();

    const stats = $('#remote-stats');
    if (stats && !state.remote.liveEnabled) {
      stats.textContent = typeof EventSource !== 'undefined'
        ? 'Ожидание кадра с телефона (SSE, без опроса)…'
        : 'Ожидание кадра…';
    }
  } finally {
    state.remote.startRemoteLock = false;
  }
}

/**
 * Синхронизирует aspect-ratio контейнера `.camera` с реальными размерами
 * кадра, пришедшего с телефона. Пока телефон шлёт вертикальные кадры
 * (portrait) — контейнер сам становится вертикальным, и картинка ложится
 * без обрезки (object-fit: contain). Это решает проблему
 * «отображение видео не совпадает с видео с телефона».
 */
function syncRemoteAspect() {
  const img = $('#remote-img');
  const cam = $('#remote-camera');
  if (!img || !cam) return;
  const w = img.naturalWidth;
  const h = img.naturalHeight;
  if (!w || !h) return;
  cam.style.setProperty('--camera-aspect', `${w} / ${h}`);
  // Оверлей необходимо перерисовать (если live-анализ активен), т.к.
  // геометрия контейнера изменилась.
  if (state.remote.liveEnabled && state.remote.lastResult) {
    drawOverlay('#remote-img', '#remote-overlay', state.remote.lastResult);
  }
}

function closeFrameEventSource() {
  if (state.remote.eventSource) {
    try { state.remote.eventSource.close(); } catch { /* … */ }
    state.remote.eventSource = null;
  }
}

function startFrameEventSource() {
  closeFrameEventSource();
  const dev = state.currentDevice;
  if (!dev || !state.token) return;
  try {
    const url = `/api/devices/${dev.id}/frame-events?token=${encodeURIComponent(state.token)}`;
    const es = new EventSource(url);
    state.remote.eventSource = es;
    es.onmessage = (ev) => {
      try {
        const d = JSON.parse(ev.data);
        if (d.type === 'status' && d.status) {
          applyRemoteStatusPayload(d.status);
          return;
        }
        if ((d.type === 'frame' && d.available) && $('#source-remote')?.classList.contains('is-active')) {
          state.remote.status = state.remote.status || {};
          state.remote.status.frame_available = true;
          if (!state.remote.status.frame_received_at) {
            state.remote.status.frame_received_at = new Date().toISOString();
          }
          state.remote.snapshotFrameMissing = false;
          state.remote.snapshotRetryNotBefore = 0;
          state.remote.snapshot404Streak = 0;
          ensureMjpegStream();
          scheduleRemoteSnapshotTick(0);
        }
      } catch { /* … */ }
    };
  } catch { /* EventSource недоступен */ }
}

function stopRemote() {
  closeFrameEventSource();
  clearRemotePollingTimers();
  state.remote.activeRemoteDeviceId = null;
  state.remote.startRemoteLock = false;
  if (state.remote.liveTimer) { clearTimeout(state.remote.liveTimer); state.remote.liveTimer = null; }
  state.remote.liveEnabled = false;
  state.remote.inflight = false;
  state.remote.snapshotBlob = null;
  state.remote.lastResult = null;
  state.remote.mjpegStarted = false;
  state.remote.snapshot404Streak = 0;
  state.remote.lastFrameAtFromServer = null;
  state.remote.snapshotFrameMissing = false;
  state.remote.snapshotRetryNotBefore = 0;
  const img = $('#remote-img');
  if (img) { img.src = ''; img.removeAttribute('data-device-id'); }
  const cam = $('#remote-camera'); if (cam) cam.style.removeProperty('--camera-aspect');
  clearOverlay('#remote-overlay');
  const st = $('#remote-stats'); if (st) st.textContent = '—';
  const chk = $('#chk-live-remote'); if (chk) chk.checked = false;
  const ctrl = $('#remote-controls'); if (ctrl) ctrl.hidden = true;
}

async function fetchRemoteSnapshot() {
  const dev = state.currentDevice;
  if (!dev) return;
  if (!hasRemoteServerFrame()) return;
  try {
    const res = await api(`/api/devices/${dev.id}/frame.jpg`, { raw: true });
    if (res.ok) {
      state.remote.snapshotBlob = await res.blob();
      state.remote.snapshot404Streak = 0;
      state.remote.snapshotFrameMissing = false;
      state.remote.snapshotRetryNotBefore = 0;
    } else if (res.status === 404) {
      state.remote.snapshot404Streak += 1;
      state.remote.snapshotFrameMissing = true;
      state.remote.snapshotRetryNotBefore = Date.now()
        + Math.min(REMOTE_SNAPSHOT_BACKOFF_MAX, 1200 + state.remote.snapshot404Streak * 900);
    }
  } catch { /* временные сетевые ошибки — тихо */ }
}

$('#chk-live-remote').addEventListener('change', (ev) => {
  state.remote.liveEnabled = ev.target.checked;
  if (state.remote.liveEnabled) scheduleRemoteLive(0);
  else {
    clearOverlay('#remote-overlay');
    if (state.remote.liveTimer) { clearTimeout(state.remote.liveTimer); state.remote.liveTimer = null; }
  }
});

function scheduleRemoteLive(delay) {
  if (!state.remote.liveEnabled) return;
  state.remote.liveTimer = setTimeout(runRemoteLive, delay);
}

async function runRemoteLive() {
  if (!state.remote.liveEnabled) return;
  if (state.remote.inflight || !state.remote.snapshotBlob) {
    scheduleRemoteLive(250);
    return;
  }
  state.remote.inflight = true;
  try {
    const blob = await downscaleBlob(state.remote.snapshotBlob, state.meta.live_analysis_max_side || 640);
    const fd = new FormData();
    fd.append('image', blob, 'live.jpg');
    appendGoldenBoardProfileId(fd);
    const t0 = performance.now();
    const res = await api('/api/inspections/live', { method: 'POST', body: fd });
    const rtt = (performance.now() - t0).toFixed(0);
    state.remote.lastResult = res;
    drawOverlay('#remote-img', '#remote-overlay', res);
    const { sd, dc } = liveSemanticCounts(res);
    $('#remote-stats').textContent = `${sd} деф. / ${dc} объекта · ${res.inference_time_ms.toFixed(0)} мс · RTT ${rtt} мс · ${res.backend}`;
  } catch (e) {
    $('#remote-stats').textContent = `Ошибка: ${e.message}`;
  } finally {
    state.remote.inflight = false;
    const interval = Math.max(300, state.meta.live_analysis_interval_ms || 1200);
    scheduleRemoteLive(interval);
  }
}

async function downscaleBlob(blob, maxSide) {
  const bmp = await createImageBitmap(blob).catch(() => null);
  if (!bmp) return blob;
  const scale = Math.min(1, maxSide / Math.max(bmp.width, bmp.height));
  const w = Math.round(bmp.width * scale), h = Math.round(bmp.height * scale);
  const cv = document.createElement('canvas');
  cv.width = w; cv.height = h;
  cv.getContext('2d').drawImage(bmp, 0, 0, w, h);
  return new Promise((resolve) => cv.toBlob(resolve, 'image/jpeg', 0.82));
}

// ---------- Подсветка стенда WLED (JSON API, настройки в БД) ----------
/** Сервер уже переставляет каналы (brg, swap_gb, custom). */
function wledServerMapsColors() {
  const o = state.wledHardwareColorOrder;
  return o === 'brg' || o === 'swap_gb' || o === 'custom';
}

/** Клиентская компенсация цикла R→B, G→R, B→G (для portable без brg в exe). */
function wledBrgClientFixEnabled() {
  if (wledServerMapsColors()) return false;
  if (state.portableBrgFix) return true;
  return localStorage.getItem('aoi_wled_brg_fix') === '1';
}

function wledMapFromCalibration(obs) {
  const slot = { r: 0, g: 1, b: 2 };
  const out = ['r', 'g', 'b'];
  for (const logical of 'rgb') {
    const seen = obs[logical];
    if (!seen) throw new Error('Укажите, что видно на ленте для всех трёх цветов палитры');
    out[slot[logical]] = seen;
  }
  if (new Set(out).size !== 3) throw new Error('На ленте должны быть три разных цвета');
  return out.join('');
}

function readWledColorCalibration() {
  const obs = {};
  for (const key of 'rgb') {
    const sel = document.querySelector(`select[data-wled-cal-key="${key}"]`);
    const v = sel?.value?.trim().toLowerCase();
    if (v) obs[key] = v;
  }
  return obs;
}

function renderWledCalMapHint(colorMap) {
  const el = $('#wled-cal-map-hint');
  if (!el) return;
  const labels = { r: 'R', g: 'G', b: 'B' };
  if (!colorMap || colorMap.length !== 3) {
    el.textContent = 'Карта API: —';
    return;
  }
  const parts = [...colorMap].map((ch, i) => `слот ${'RGB'[i]}←${labels[ch] || ch}`);
  el.textContent = `Карта API: ${colorMap} (${parts.join(', ')})`;
}

function fillWledColorCalibrationForm(colorCal, colorMap) {
  for (const key of 'rgb') {
    const sel = document.querySelector(`select[data-wled-cal-key="${key}"]`);
    if (sel && colorCal?.[key]) sel.value = colorCal[key];
  }
  renderWledCalMapHint(colorMap);
}

function detectPortableBrgFix() {
  if (state.portableBrgFix != null) return;
  state.portableBrgFix = false;
  fetch('/static/aoi_brg_fix.on', { cache: 'no-store' })
    .then((r) => {
      state.portableBrgFix = r.ok;
    })
    .catch(() => {});
}
detectPortableBrgFix();

function _hexToRgb(hex) {
  const h = String(hex || '').trim().toLowerCase().replace('#', '');
  if (h.length !== 6) return null;
  return [parseInt(h.slice(0, 2), 16), parseInt(h.slice(2, 4), 16), parseInt(h.slice(4, 6), 16)];
}

function _rgbToHex(r, g, b) {
  const t = (n) => Math.max(0, Math.min(255, n | 0)).toString(16).padStart(2, '0');
  return `#${t(r)}${t(g)}${t(b)}`;
}

/** Цвет с палитры → что слать в API, чтобы на ленте совпало. */
function wledBrgWireToApiHex(hex) {
  const c = _hexToRgb(hex);
  if (!c) return hex;
  return _rgbToHex(c[2], c[0], c[1]);
}

/** Ответ API → значение для input[type=color]. */
function wledBrgApiToWireHex(hex) {
  const c = _hexToRgb(hex);
  if (!c) return hex;
  return _rgbToHex(c[1], c[2], c[0]);
}

function esp32ControlPayload(extra = {}) {
  const brightness = parseInt($('#esp32-brightness')?.value ?? '80', 10);
  let color = $('#esp32-color')?.value || '#ffffff';
  if (wledBrgClientFixEnabled()) color = wledBrgWireToApiHex(color);
  return { brightness, color: color.toLowerCase(), ...extra };
}

function renderEsp32HardwareStatus(data) {
  const line = $('#esp32-status-line');
  const panel = document.querySelector('.esp32-panel');
  if (!line || !panel) return;
  state.hardwareStatus = data;

  const controls = panel.querySelectorAll(
    '[data-esp32-preset], #btn-esp32-probe, #btn-esp32-apply, #esp32-brightness, #esp32-color',
  );
  const enabled = Boolean(data?.esp32_enabled);

  if (!enabled) {
    line.textContent = isAdminRole()
      ? 'Статус: WLED выключен — откройте «Настройки подключения WLED», укажите адрес и включите.'
      : 'Статус: подсветка стенда не настроена. Обратитесь к администратору.';
    line.className = 'hint';
    controls.forEach((el) => { el.disabled = true; });
    return;
  }

  controls.forEach((el) => { el.disabled = false; });

  if (!data.esp32_configured) {
    line.textContent = isAdminRole()
      ? 'Статус: укажите адрес WLED и сохраните настройки.'
      : 'Статус: подсветка не настроена. Обратитесь к администратору.';
    line.className = 'hint warn';
    return;
  }

  const adminUrl = isAdminRole() && state.wledAdminDiag?.esp32_base_url
    ? ` (${state.wledAdminDiag.esp32_base_url})`
    : '';
  const url = adminUrl;
  if (data.esp32_reachable === true) {
    const ms = data.esp32_latency_ms != null ? `, ${Math.round(data.esp32_latency_ms)} мс` : '';
    line.textContent = `Статус: WLED в сети${url}${ms}`;
    line.className = 'hint success';
  } else if (data.esp32_reachable === false) {
    line.textContent = `Статус: WLED недоступен${url} — ${data.esp32_probe_message || 'нет ответа'}`;
    line.className = 'hint warn';
  } else {
    line.textContent = `Статус: проверка…${url}`;
    line.className = 'hint';
  }

  if (data.active_brightness != null && $('#esp32-brightness')) {
    $('#esp32-brightness').value = String(data.active_brightness);
    const bv = $('#esp32-brightness-v');
    if (bv) bv.textContent = String(data.active_brightness);
  }
  if (data.active_color && $('#esp32-color')) {
    let c = data.active_color;
    if (wledBrgClientFixEnabled()) c = wledBrgApiToWireHex(c);
    $('#esp32-color').value = c;
  }

  document.querySelectorAll('[data-esp32-preset]').forEach((btn) => {
    btn.classList.toggle('is-active', btn.dataset.esp32Preset === data.active_preset);
  });
}

async function loadEsp32HardwareConfig() {
  if (!isAdminRole()) return;
  const form = $('#esp32-config-form');
  if (!form) return;
  try {
    const cfg = await api('/api/pipeline/hardware/config');
    form.elements.enabled.checked = Boolean(cfg.enabled);
    form.elements.base_url.value = cfg.base_url || '';
    const mode = cfg.connection_mode || 'manual';
    const modeEl = form.querySelector(`input[name="connection_mode"][value="${mode}"]`);
    if (modeEl) modeEl.checked = true;
    form.elements.health_path.value = cfg.health_path || '/json/info';
    form.elements.control_path.value = cfg.control_path || '/json/state';
    if (form.elements.segment_id) form.elements.segment_id.value = cfg.segment_id ?? 0;
    if (form.elements.transition) form.elements.transition.value = cfg.transition ?? 7;
    state.wledHardwareColorOrder = cfg.color_order || 'rgb';
    state.wledHardwareColorMap = cfg.color_map || 'rgb';
    const colorOrder = $('#esp32-color-order');
    if (colorOrder) colorOrder.value = state.wledHardwareColorOrder;
    fillWledColorCalibrationForm(cfg.color_cal, cfg.color_map);
    form.elements.timeout_sec.value = cfg.timeout_sec ?? 2.5;
    await refreshWledAdminDiagnostics();
  } catch (e) {
    const msg = $('#esp32-config-msg');
    if (msg) {
      msg.className = 'status error';
      msg.textContent = e.message;
    }
  }
}

async function saveEsp32HardwareConfig(ev) {
  ev?.preventDefault();
  if (!isAdminRole()) return;
  const form = $('#esp32-config-form');
  const msg = $('#esp32-config-msg');
  if (!form) return;
  const fd = new FormData(form);
  const connectionMode = form.querySelector('input[name="connection_mode"]:checked')?.value || 'manual';
  const body = {
    enabled: form.elements.enabled.checked,
    connection_mode: connectionMode,
    base_url: String(fd.get('base_url') || '').trim(),
    health_path: String(fd.get('health_path') || '/json/info').trim(),
    control_path: String(fd.get('control_path') || '/json/state').trim(),
    segment_id: parseInt(fd.get('segment_id') || '0', 10),
    transition: parseInt(fd.get('transition') || '7', 10),
    color_order: String(fd.get('color_order') || form.elements.color_order?.value || 'rgb'),
    color_map: state.wledHardwareColorMap || 'rgb',
    color_cal: readWledColorCalibration(),
    timeout_sec: parseFloat(fd.get('timeout_sec') || '2.5'),
  };
  if (body.color_order !== 'custom') {
    delete body.color_cal;
  }
  if (msg) {
    msg.className = 'status';
    msg.textContent = 'Сохранение…';
  }
  try {
    if (body.enabled && body.connection_mode === 'auto' && !body.base_url) {
      const found = await api('/api/pipeline/hardware/discover', {
        method: 'POST',
        body: { use_mdns: true, use_nodes: true },
      });
      const ok = (found.devices || []).filter((d) => d.reachable);
      if (ok.length) {
        body.base_url = ok[0].base_url;
        if (form.elements.base_url) form.elements.base_url.value = body.base_url;
      }
    }
    await api('/api/pipeline/hardware/config', { method: 'PUT', body });
    if (msg) {
      msg.className = 'status success';
      msg.textContent = 'Настройки сохранены';
    }
    await refreshEsp32HardwareStatus(true);
    await refreshWledAdminDiagnostics();
  } catch (e) {
    if (msg) {
      msg.className = 'status error';
      msg.textContent = e.message;
    }
  }
}

async function refreshEsp32HardwareStatus(forceProbe) {
  const msg = $('#esp32-msg');
  try {
    const path = forceProbe ? '/api/pipeline/hardware/probe' : '/api/pipeline/hardware/status';
    const data = await api(path, forceProbe ? { method: 'POST' } : undefined);
    renderEsp32HardwareStatus(data);
    if (msg) {
      msg.className = 'status';
      msg.textContent = data.last_error ? `Ошибка: ${data.last_error}` : '';
    }
  } catch (e) {
    if (msg) {
      msg.className = 'status error';
      msg.textContent = e.message;
    }
  }
}

async function applyEsp32LightingControl(extra = {}) {
  const msg = $('#esp32-msg');
  if (msg) {
    msg.className = 'status';
    msg.textContent = 'Отправка команды…';
  }
  try {
    const body = esp32ControlPayload(extra);
    const res = await api('/api/pipeline/lighting/control', { method: 'POST', body });
    if (msg) {
      msg.className = 'status success';
      msg.textContent = 'Подсветка применена';
    }
    if (res.preset) {
      document.querySelectorAll('[data-esp32-preset]').forEach((btn) => {
        btn.classList.toggle('is-active', btn.dataset.esp32Preset === res.preset);
      });
    }
    await refreshEsp32HardwareStatus(false);
    if (isAdminRole()) await refreshWledAdminDiagnostics();
  } catch (e) {
    if (msg) {
      msg.className = 'status error';
      msg.textContent = e.message;
    }
  }
}

$('#esp32-config-form')?.addEventListener('submit', saveEsp32HardwareConfig);

$('#esp32-brightness')?.addEventListener('input', (ev) => {
  const v = ev.target.value;
  const el = $('#esp32-brightness-v');
  if (el) el.textContent = v;
});

document.addEventListener('click', (ev) => {
  const btn = ev.target.closest('[data-esp32-preset]');
  if (!btn || btn.disabled) return;
  const preset = btn.dataset.esp32Preset;
  if (preset) applyEsp32LightingControl({ preset });
});

$('#btn-esp32-apply')?.addEventListener('click', () => applyEsp32LightingControl({}));
$('#btn-esp32-probe')?.addEventListener('click', () => refreshEsp32HardwareStatus(true));

document.querySelectorAll('.wled-cal-test').forEach((btn) => {
  btn.addEventListener('click', async () => {
    const hex = btn.dataset.wledCal;
    const picker = $('#esp32-color');
    if (picker && hex) picker.value = hex;
    await applyEsp32LightingControl({ preset: 'rgb_highlight', brightness: 100 });
  });
});

$('#btn-wled-cal-apply')?.addEventListener('click', async () => {
  const msg = $('#wled-cal-msg');
  const form = $('#esp32-config-form');
  const orderSel = $('#esp32-color-order');
  try {
    const obs = readWledColorCalibration();
    if (Object.keys(obs).length !== 3) {
      throw new Error('Заполните «На ленте вижу» для красного, зелёного и синего');
    }
    const colorMap = wledMapFromCalibration(obs);
    state.wledHardwareColorMap = colorMap;
    state.wledHardwareColorOrder = 'custom';
    if (orderSel) orderSel.value = 'custom';
    renderWledCalMapHint(colorMap);
    if (msg) {
      msg.className = 'status success';
      msg.textContent = `Карта ${colorMap} — сохраните настройки WLED`;
    }
    if (form) {
      await saveEsp32HardwareConfig(new Event('submit'));
    }
  } catch (e) {
    if (msg) {
      msg.className = 'status error';
      msg.textContent = e.message;
    }
  }
});

$('#esp32-color-order')?.addEventListener('change', (ev) => {
  const v = ev.target.value;
  state.wledHardwareColorOrder = v;
  if (v === 'brg') state.wledHardwareColorMap = 'brg';
  else if (v === 'swap_gb') state.wledHardwareColorMap = 'rbg';
  else if (v === 'rgb') state.wledHardwareColorMap = 'rgb';
  renderWledCalMapHint(state.wledHardwareColorMap);
});

function renderWledDiscoverList(devices) {
  const el = $('#wled-discover-list');
  if (!el) return;
  if (!devices?.length) {
    el.textContent = 'Устройства не найдены. Проверьте Wi‑Fi, mDNS и адрес вручную.';
    return;
  }
  el.innerHTML = devices.map((d) => {
    const ok = d.reachable ? '✓' : '✗';
    const label = `${ok} ${d.name || d.ip} — ${d.base_url} (${d.source})`;
    return `<button type="button" class="btn btn--ghost wled-pick-device" data-url="${d.base_url}" style="display:block;margin:4px 0;text-align:left">${label}</button>`;
  }).join('');
}

async function discoverWledOnLan() {
  const msg = $('#wled-discover-msg');
  const form = $('#esp32-config-form');
  if (!isAdminRole() || !form) return;
  if (msg) {
    msg.className = 'status';
    msg.textContent = 'Поиск… (mDNS и /json/nodes)';
  }
  try {
    const seed = String(form.elements.base_url?.value || '').trim();
    const res = await api('/api/pipeline/hardware/discover', {
      method: 'POST',
      body: {
        seed_base_url: seed || null,
        use_mdns: true,
        use_nodes: true,
      },
    });
    renderWledDiscoverList(res.devices);
    if (msg) {
      const n = (res.devices || []).filter((d) => d.reachable).length;
      const methods = (res.methods_used || []).join(', ') || '—';
      msg.className = 'status success';
      msg.textContent = `Найдено: ${res.devices?.length || 0}, доступно: ${n} (${Math.round(res.duration_ms)} мс, ${methods})`;
      if (res.errors?.length) {
        msg.textContent += ` — ${res.errors.join('; ')}`;
      }
    }
    const reachable = (res.devices || []).filter((d) => d.reachable);
    if (reachable.length === 1 && form.elements.base_url && !form.elements.base_url.value.trim()) {
      form.elements.base_url.value = reachable[0].base_url;
    }
  } catch (e) {
    if (msg) {
      msg.className = 'status error';
      msg.textContent = e.message;
    }
  }
}

document.addEventListener('click', (ev) => {
  const pick = ev.target.closest('.wled-pick-device');
  if (!pick) return;
  const url = pick.dataset.url;
  const input = $('#esp32-base-url');
  if (input && url) input.value = url;
});

function renderWledAdminDebug(diag) {
  state.wledAdminDiag = diag;
  const last = $('#wled-debug-last');
  const st = $('#wled-debug-state');
  const log = $('#wled-debug-log');
  if (st) {
    st.textContent = diag?.last_wled_state
      ? JSON.stringify(diag.last_wled_state, null, 2)
      : '—';
  }
  const exchanges = diag?.debug_exchanges || [];
  if (last && exchanges.length) {
    const ex = exchanges[exchanges.length - 1];
    last.textContent = JSON.stringify(ex, null, 2);
  } else if (last) {
    last.textContent = '—';
  }
  if (log) {
    log.textContent = exchanges.length
      ? exchanges.map((ex) => `${ex.method} ${ex.url} → ${ex.status_code} ok=${ex.ok}\n${JSON.stringify(ex.response_body, null, 2)}`).join('\n\n---\n\n')
      : (diag?.recent_commands?.join('\n') || '—');
  }
}

async function refreshWledAdminDiagnostics() {
  if (!isAdminRole()) return;
  try {
    const diag = await api('/api/pipeline/hardware/admin/diagnostics');
    renderWledAdminDebug(diag);
    if (state.hardwareStatus) {
      renderEsp32HardwareStatus(state.hardwareStatus);
    }
  } catch (_e) {
    /* ignore when not admin or offline */
  }
}

async function sendWledDebugRequest(method, path, body) {
  const msg = $('#wled-debug-msg');
  if (msg) {
    msg.className = 'status';
    msg.textContent = 'Запрос…';
  }
  try {
    const res = await api('/api/pipeline/hardware/admin/debug-request', {
      method: 'POST',
      body: {
        method,
        path,
        body: body ?? null,
        base_url: $('#esp32-base-url')?.value?.trim() || null,
      },
    });
    const last = $('#wled-debug-last');
    if (last) last.textContent = JSON.stringify(res, null, 2);
    if (msg) {
      msg.className = res.ok ? 'status success' : 'status error';
      msg.textContent = res.ok
        ? `OK ${res.status_code} (${Math.round(res.latency_ms || 0)} мс)`
        : (res.error || `HTTP ${res.status_code}`);
    }
    await refreshWledAdminDiagnostics();
    await refreshEsp32HardwareStatus(false);
  } catch (e) {
    if (msg) {
      msg.className = 'status error';
      msg.textContent = e.message;
    }
  }
}

$('#btn-wled-discover')?.addEventListener('click', discoverWledOnLan);

$('#btn-wled-debug-refresh')?.addEventListener('click', () => refreshWledAdminDiagnostics());

$('#btn-wled-debug-send')?.addEventListener('click', () => {
  const path = $('#wled-debug-path')?.value?.trim() || '/json/state';
  const raw = $('#wled-debug-body')?.value?.trim();
  let body = null;
  let method = 'GET';
  if (raw) {
    method = 'POST';
    try {
      body = JSON.parse(raw);
    } catch (e) {
      const msg = $('#wled-debug-msg');
      if (msg) {
        msg.className = 'status error';
        msg.textContent = `Неверный JSON: ${e.message}`;
      }
      return;
    }
  }
  sendWledDebugRequest(method, path, body);
});

document.addEventListener('click', (ev) => {
  const btn = ev.target.closest('.wled-debug-preset');
  if (!btn) return;
  const path = btn.dataset.wledPath;
  const method = btn.dataset.wledMethod || 'GET';
  const pathInput = $('#wled-debug-path');
  const bodyInput = $('#wled-debug-body');
  if (pathInput) pathInput.value = path;
  let body = null;
  if (btn.dataset.wledBody) {
    try {
      body = JSON.parse(btn.dataset.wledBody);
    } catch (_e) {
      body = null;
    }
    if (bodyInput) bodyInput.value = btn.dataset.wledBody;
  } else if (bodyInput) {
    bodyInput.value = method === 'POST' ? '{"v":true}' : '';
  }
  sendWledDebugRequest(method, path, body);
});

// ---------- Удалённое управление телефоном (PC → phone) ----------
async function sendDeviceCommand(command, value) {
  const dev = state.currentDevice;
  if (!dev) return;
  const msg = $('#rc-msg');
  try {
    await api(`/api/devices/${dev.id}/control`, {
      method: 'POST',
      body: { command, value: value ?? null },
    });
    if (msg) msg.textContent = `✓ ${command}${value ? ' = ' + value : ''}`;
  } catch (e) {
    if (msg) msg.textContent = `Ошибка: ${e.message}`;
  }
}

function updateRemoteControlsUI() {
  const s = state.remote.status || {};
  const rec = $('#rc-rec');
  if (rec) {
    rec.textContent = s.is_streaming ? 'Остановить запись' : 'Начать запись';
    rec.classList.toggle('btn--danger', !!s.is_streaming);
    rec.classList.toggle('btn--primary', !s.is_streaming);
  }
  // Подсветка активного пресета.
  const key = s.preset || state.remote.presetKey;
  document.querySelectorAll('#rc-quality .chip-preset').forEach((btn) => {
    btn.classList.toggle('is-active', btn.dataset.preset === key);
  });
}

// Делегирование кликов по всей панели управления.
document.addEventListener('click', (ev) => {
  const btn = ev.target.closest('#remote-controls [data-rc], #remote-controls [data-preset]');
  if (!btn) return;
  if (btn.dataset.preset) {
    sendDeviceCommand('quality', btn.dataset.preset);
    return;
  }
  const act = btn.dataset.rc;
  if (act === 'toggle') {
    const streaming = !!(state.remote.status && state.remote.status.is_streaming);
    sendDeviceCommand(streaming ? 'stop' : 'start');
  } else if (act === 'torch_on')  sendDeviceCommand('torch_on');
  else if (act === 'torch_off') sendDeviceCommand('torch_off');
  else if (act === 'flip')      sendDeviceCommand('flip');
});

// Ползунок яркости — чистая клиентская правка, меняет только CSS-переменные,
// исходные кадры на сервер не трогаем.
const BRIGHTNESS_KEY = 'aoi-remote-brightness';
function applyRemoteBrightness(v) {
  const cam = $('#remote-camera');
  if (!cam) return;
  cam.style.setProperty('--remote-brightness', String(v));
  // Мягко повышаем и контраст, чтобы не было «мыла» при сильной яркости.
  cam.style.setProperty('--remote-contrast', String(1 + (v - 1) * 0.25));
}

(function initRemoteBrightness() {
  const input = $('#rc-brightness');
  const reset = $('#rc-bright-reset');
  if (!input) return;
  const saved = parseFloat(localStorage.getItem(BRIGHTNESS_KEY) || '1');
  input.value = isFinite(saved) ? saved : 1;
  applyRemoteBrightness(input.value);
  input.addEventListener('input', () => {
    applyRemoteBrightness(input.value);
    localStorage.setItem(BRIGHTNESS_KEY, input.value);
  });
  reset?.addEventListener('click', () => {
    input.value = 1;
    applyRemoteBrightness(1);
    localStorage.setItem(BRIGHTNESS_KEY, '1');
  });
})();

const PREVIEW_ZOOM_KEY = 'aoi-preview-zoom';
const PREVIEW_ZOOM_MIN = 1;
const PREVIEW_ZOOM_MAX = 3;

function clampPreviewZoom(z) {
  const n = Number(z);
  if (!Number.isFinite(n)) return PREVIEW_ZOOM_MIN;
  return Math.min(PREVIEW_ZOOM_MAX, Math.max(PREVIEW_ZOOM_MIN, n));
}

function redrawInspectPreviewOverlays() {
  if (state.remote.liveEnabled && state.remote.lastResult) {
    drawOverlay('#remote-img', '#remote-overlay', state.remote.lastResult);
  }
  if (state.live.enabled && state.live.lastResult) {
    drawOverlay('#video', '#overlay', state.live.lastResult);
  }
}

function setPreviewZoom(raw, { persist = true, syncInputs = true } = {}) {
  const v = clampPreviewZoom(raw);
  state.previewZoom = v;
  const rcam = $('#remote-camera');
  const lcam = $('#local-camera');
  if (rcam) rcam.style.setProperty('--camera-zoom', String(v));
  if (lcam) lcam.style.setProperty('--camera-zoom', String(v));
  if (syncInputs) {
    const ri = $('#remote-preview-zoom');
    const li = $('#local-preview-zoom');
    if (ri) ri.value = String(v);
    if (li) li.value = String(v);
  }
  if (persist) {
    try { localStorage.setItem(PREVIEW_ZOOM_KEY, String(v)); } catch { /* ignore */ }
  }
  redrawInspectPreviewOverlays();
}

(function initInspectPreviewZoom() {
  let saved = parseFloat(localStorage.getItem(PREVIEW_ZOOM_KEY) || '1');
  if (!Number.isFinite(saved)) saved = 1;
  setPreviewZoom(saved, { persist: false });

  const wireSlider = (el) => {
    if (!el) return;
    el.addEventListener('input', () => setPreviewZoom(el.value));
  };
  wireSlider($('#remote-preview-zoom'));
  wireSlider($('#local-preview-zoom'));
  $('#rc-zoom-reset')?.addEventListener('click', () => setPreviewZoom(1));
  $('#local-zoom-reset')?.addEventListener('click', () => setPreviewZoom(1));

  const bindGestures = (cam) => {
    if (!cam || cam.dataset.previewZoomBound) return;
    cam.dataset.previewZoomBound = '1';
    let pinchStartDist = 0;
    let pinchStartZoom = 1;
    cam.addEventListener('touchstart', (ev) => {
      if (ev.touches.length === 2) {
        const a = ev.touches[0];
        const b = ev.touches[1];
        pinchStartDist = Math.hypot(a.clientX - b.clientX, a.clientY - b.clientY);
        pinchStartZoom = state.previewZoom;
      }
    }, { passive: true });
    cam.addEventListener('touchmove', (ev) => {
      if (ev.touches.length !== 2 || pinchStartDist < 10) return;
      ev.preventDefault();
      const a = ev.touches[0];
      const b = ev.touches[1];
      const d = Math.hypot(a.clientX - b.clientX, a.clientY - b.clientY);
      setPreviewZoom(pinchStartZoom * (d / pinchStartDist));
    }, { passive: false });
    cam.addEventListener('touchend', (ev) => {
      if (ev.touches.length < 2) pinchStartDist = 0;
    });
    cam.addEventListener('wheel', (ev) => {
      const pane = cam.closest('.source-pane');
      if (pane && !pane.classList.contains('is-active')) return;
      ev.preventDefault();
      const factor = Math.exp(-ev.deltaY * 0.002);
      setPreviewZoom(state.previewZoom * factor);
    }, { passive: false });
  };
  bindGestures($('#remote-camera'));
  bindGestures($('#local-camera'));
})();

$('#btn-capture-remote').addEventListener('click', async () => {
  if (!state.remote.snapshotBlob) {
    // Попытка подтянуть кадр сразу.
    await fetchRemoteSnapshot();
  }
  if (!state.remote.snapshotBlob) {
    setStatus('Кадр ещё не получен — убедитесь, что на телефоне запущена трансляция.', 'warn');
    return;
  }
  await submitImage(state.remote.snapshotBlob, `remote_${Date.now()}.jpg`);
});

// ---------- Оверлей рамок (над <video> или <img>) ----------
function clearOverlay(sel) {
  const cv = $(sel);
  if (!cv) return;
  cv.getContext('2d').clearRect(0, 0, cv.width, cv.height);
}

function drawOverlay(sourceSel, canvasSel, result) {
  const src = $(sourceSel);
  const cv = $(canvasSel);
  if (!src || !cv) return;
  const rect = src.getBoundingClientRect();
  cv.width = rect.width;
  cv.height = rect.height;
  const ctx = cv.getContext('2d');
  ctx.clearRect(0, 0, cv.width, cv.height);

  const natW = src.videoWidth || src.naturalWidth || result.image_width;
  const natH = src.videoHeight || src.naturalHeight || result.image_height;
  if (!natW || !natH) return;

  // Определяем режим масштабирования: video (локальная камера) — cover,
  // img#remote-img (поток телефона) — contain (чтобы кадр отображался целиком
  // и совпадал с тем, что видит телефон).
  const isImg = src.tagName === 'IMG';
  const scale = isImg
    ? Math.min(cv.width / natW, cv.height / natH)   // contain (letterbox)
    : Math.max(cv.width / natW, cv.height / natH);  // cover (video)
  const offX = (cv.width - natW * scale) / 2;
  const offY = (cv.height - natH * scale) / 2;
  const kx = natW / result.image_width;
  const ky = natH / result.image_height;

  ctx.fillStyle = 'rgba(0,0,0,0.55)';
  ctx.fillRect(0, 0, cv.width, cv.height);
  ctx.globalCompositeOperation = 'destination-out';
  for (const d of result.defects) {
    const x = offX + d.bbox_x1 * kx * scale;
    const y = offY + d.bbox_y1 * ky * scale;
    const w = (d.bbox_x2 - d.bbox_x1) * kx * scale;
    const h = (d.bbox_y2 - d.bbox_y1) * ky * scale;
    ctx.fillRect(x, y, w, h);
  }
  ctx.globalCompositeOperation = 'source-over';

  const colorMap = Object.fromEntries(state.defectClasses.map((c) => [c.code, c.color]));
  ctx.lineWidth = Math.max(2, cv.width / 400);
  ctx.font = `${Math.max(12, cv.width / 60)}px system-ui, sans-serif`;
  ctx.textBaseline = 'top';

  for (const d of result.defects) {
    const x = offX + d.bbox_x1 * kx * scale;
    const y = offY + d.bbox_y1 * ky * scale;
    const w = (d.bbox_x2 - d.bbox_x1) * kx * scale;
    const h = (d.bbox_y2 - d.bbox_y1) * ky * scale;
    const color = colorMap[d.class_code] || '#00ff00';
    ctx.strokeStyle = color;
    ctx.strokeRect(x, y, w, h);
    const label = `${d.class_code} ${d.confidence.toFixed(2)}`;
    const metrics = ctx.measureText(label);
    const padX = 4, padY = 3;
    const lh = parseInt(ctx.font, 10) + padY * 2;
    ctx.fillStyle = color;
    ctx.fillRect(x, Math.max(0, y - lh), metrics.width + padX * 2, lh);
    ctx.fillStyle = '#000';
    ctx.fillText(label, x + padX, Math.max(0, y - lh) + padY);
  }
}

// ---------- Загрузка файла ----------
const dropzone = $('.dropzone');
const fileInput = $('#file-input');
fileInput.addEventListener('change', () => {
  const f = fileInput.files[0];
  if (f) submitImage(f, f.name);
});
['dragenter', 'dragover'].forEach((ev) =>
  dropzone.addEventListener(ev, (e) => { e.preventDefault(); dropzone.classList.add('drag'); })
);
['dragleave', 'drop'].forEach((ev) =>
  dropzone.addEventListener(ev, (e) => { e.preventDefault(); dropzone.classList.remove('drag'); })
);
dropzone.addEventListener('drop', (e) => {
  const f = e.dataTransfer.files?.[0];
  if (f) submitImage(f, f.name);
});

function appendGoldenBoardProfileId(fd) {
  const gbp = $('#golden-board-profile-id')?.value?.trim();
  if (gbp && /^\d+$/.test(gbp)) fd.append('golden_board_profile_id', gbp);
}

async function loadGoldenProfileChoices() {
  const sel = $('#golden-board-profile-id');
  if (!sel) return;
  const prev = sel.value;
  try {
    const rows = await api('/api/golden-boards/choices');
    sel.innerHTML = '<option value="">— без эталона —</option>';
    rows.forEach((r) => {
      const opt = document.createElement('option');
      opt.value = String(r.id);
      const model = r.board_model ? ` · ${r.board_model}` : '';
      opt.textContent = `${r.name} (№${r.id})${model}`;
      sel.appendChild(opt);
    });
    if (prev && [...sel.options].some((o) => o.value === prev)) sel.value = prev;
  } catch {
    /* список эталонов недоступен — оставляем только «без эталона» */
  }
}

// ---------- Отправка изображения ----------
function setStatus(text, kind = '') {
  const el = $('#inspect-status');
  el.className = 'status ' + kind;
  el.textContent = text;
}

async function submitImage(blob, filename) {
  const card = $('#inspect-result');
  card.hidden = true;
  setStatus('Выполняется инспекция…');
  try {
    const fd = new FormData();
    fd.append('image', blob, filename);
    if (state.currentDevice) fd.append('device_id', state.currentDevice.id);
    const bm = $('#board-model-input')?.value?.trim();
    if (bm) fd.append('board_model', bm);
    appendGoldenBoardProfileId(fd);
    const data = await api('/api/inspections', { method: 'POST', body: fd });
    const det = data.detections_count != null ? data.detections_count : (data.defects?.length ?? 0);
    const defectMsg =
      det > (data.defects_count ?? 0)
        ? `Дефектов (по классу): ${data.defects_count}, всего объектов: ${det}.`
        : `Дефектов (по классу): ${data.defects_count}.`;
    setStatus(
      data.status === 'success'
        ? `Инспекция №${data.id} завершена. ${defectMsg}`
        : `Инспекция №${data.id} завершилась с ошибкой: ${data.error_message}`,
      data.status === 'success' ? 'success' : 'error'
    );
    revealInspectPanelForOperator();
    renderInspection(data);
  } catch (e) {
    setStatus(e.message, 'error');
  }
}

function renderInspection(data) {
  state.currentInspection = data;
  $('#inspect-result').hidden = false;
  $('#res-id').textContent = data.id;
  $('#res-date').textContent = formatDate(data.created_at);
  $('#res-device').textContent = data.device_name || '—';
  $('#res-filename').textContent = data.original_filename;
  $('#res-resolution').textContent = data.image_width ? `${data.image_width} × ${data.image_height}` : '—';
  $('#res-conf').textContent = data.conf_threshold ? data.conf_threshold.toFixed(2) : '—';
  $('#res-count').textContent = data.defects_count;
  const rd = $('#res-detections');
  if (rd) {
    rd.textContent =
      data.detections_count != null ? data.detections_count : (data.defects?.length ?? 0);
  }
  $('#res-confidence').textContent = data.avg_confidence ? data.avg_confidence.toFixed(3) : '—';
  $('#res-time').textContent = data.inference_time_ms ? `${data.inference_time_ms.toFixed(1)} мс` : '—';
  const rbm = $('#res-board-model');
  if (rbm) rbm.textContent = data.board_model || '—';
  const rgp = $('#res-golden-profile');
  if (rgp) {
    rgp.textContent =
      data.golden_board_profile_id != null ? String(data.golden_board_profile_id) : '—';
  }
  const rga = $('#res-golden-align');
  if (rga) {
    if (data.golden_board_profile_id == null) {
      rga.textContent = '—';
    } else if (data.golden_alignment_used) {
      const b = data.alignment_mae_before != null ? data.alignment_mae_before.toFixed(3) : '?';
      const a = data.alignment_mae_after != null ? data.alignment_mae_after.toFixed(3) : '?';
      rga.textContent = `да, MAE ${b} → ${a}`;
    } else if (data.alignment_mae_before != null) {
      const b = data.alignment_mae_before.toFixed(3);
      const a = data.alignment_mae_after != null ? data.alignment_mae_after.toFixed(3) : '?';
      rga.textContent = `нет ECC; сверка по масштабу (MAE ${b} → ${a})`;
    } else {
      rga.textContent = 'нет (опорный снимок не задан или недоступен)';
    }
  }
  const img = $('#result-image');
  const origUrl = data.original_url || data.result_url;
  if (img?.src?.startsWith('blob:')) {
    try {
      URL.revokeObjectURL(img.src);
    } catch (_) {
      /* ignore */
    }
  }
  if (img) {
    loadImageWithAuth(origUrl, img)
      .then(async () => {
        try {
          if (img.decode) await img.decode();
        } catch {
          /* ignore */
        }
        resetResultView();
        requestAnimationFrame(() => redrawResultDefectOverlay());
      })
      .catch(() => {
        resetResultView();
        requestAnimationFrame(() => redrawResultDefectOverlay());
      });
  }

  $('#btn-pdf').href = `/api/inspections/${data.id}/export/pdf`;
  $('#btn-csv').href = `/api/inspections/${data.id}/export/csv`;
  $('#btn-pdf').onclick = (e) => downloadWithAuth(e, `/api/inspections/${data.id}/export/pdf`, `inspection_${data.id}.pdf`);
  $('#btn-csv').onclick = (e) => downloadWithAuth(e, `/api/inspections/${data.id}/export/csv`, `inspection_${data.id}.csv`);
  const trainingBtn = $('#btn-training-zip');
  if (trainingBtn) {
    const canDownloadTraining = !!data.training_dir;
    trainingBtn.hidden = !canDownloadTraining;
    trainingBtn.href = `/api/inspections/${data.id}/export/training.zip`;
    trainingBtn.onclick = canDownloadTraining
      ? (e) => downloadWithAuth(e, `/api/inspections/${data.id}/export/training.zip`, `inspection_${data.id}_training.zip`)
      : null;
  }

  updateReviewButton(data);

  renderDefectsList(data);
}

function updateReviewButton(data) {
  const btn = $('#btn-review');
  const btnRec = $('#btn-review-recognition');
  const status = $('#review-status');
  if (!btn) return;
  const defectQueue = defectsForReviewQueue(data.defects);
  const recognitionQueue = recognitionForReviewQueue(data.defects);
  const hasAnyDetection = (data.defects?.length || 0) > 0;
  btn.hidden = defectQueue.length === 0;
  if (btnRec) btnRec.hidden = recognitionQueue.length === 0;
  btn.textContent = data.reviewed_at ? 'Проверить дефекты повторно' : 'Оценить дефекты';
  if (btnRec) {
    btnRec.textContent = data.reviewed_at ? 'Проверить распознавание повторно' : 'Проверить распознавание';
  }
  if (!hasAnyDetection) {
    status.textContent = '';
  } else if (defectQueue.length === 0 && recognitionQueue.length === 0) {
    status.textContent =
      'По правилам семантики классов ручная оценка для этого результата не требуется.';
  } else if (data.reviewed_at) {
    const real = data.defects.filter((d) => d.is_real_defect).length;
    const rej = data.defects.filter((d) => d.is_reviewed && !d.is_real_defect).length;
    status.textContent = `Проверено ${formatDate(data.reviewed_at)} · подтверждено: ${real}, отклонено: ${rej}.`;
  } else {
    const parts = [];
    if (defectQueue.length) {
      parts.push(`дефектов для оценки: ${defectQueue.length}`);
    }
    if (recognitionQueue.length) {
      parts.push(`компонентов для проверки: ${recognitionQueue.length}`);
    }
    status.textContent = parts.join(' · ') + '.';
  }
}

async function loadImageWithAuth(url, imgEl) {
  const res = await api(url, { raw: true });
  const blob = await res.blob();
  imgEl.src = URL.createObjectURL(blob);
}

function resetResultView() {
  state.resultView.zoom = 1;
  state.resultView.pan = { x: 0, y: 0 };
  const r = $('#result-zoom-range');
  if (r) r.value = '1';
  applyResultTransform();
}

function applyResultTransform() {
  const stack = $('#result-view-stack');
  if (!stack) return;
  const { x, y } = state.resultView.pan;
  const z = state.resultView.zoom;
  stack.style.transform = `translate(calc(-50% + ${x}px), calc(-50% + ${y}px)) scale(${z})`;
}

function setResultZoom(z) {
  state.resultView.zoom = Math.max(1, Math.min(8, z));
  const r = $('#result-zoom-range');
  if (r) r.value = String(state.resultView.zoom);
  applyResultTransform();
}

(function initResultViewport() {
  const vp = $('#result-viewport');
  if (!vp) return;
  vp.addEventListener('wheel', (ev) => {
    const ir = $('#inspect-result');
    if (!ir || ir.hidden) return;
    ev.preventDefault();
    const delta = -ev.deltaY * 0.0025;
    setResultZoom(state.resultView.zoom * (1 + delta));
  }, { passive: false });

  const startDrag = (x, y) => {
    state.resultView.drag = { x, y, startX: state.resultView.pan.x, startY: state.resultView.pan.y };
    vp.classList.add('is-grabbing');
  };
  const moveDrag = (x, y) => {
    const d = state.resultView.drag;
    if (!d) return;
    state.resultView.pan = { x: d.startX + (x - d.x), y: d.startY + (y - d.y) };
    applyResultTransform();
  };
  const endDrag = () => {
    state.resultView.drag = null;
    vp.classList.remove('is-grabbing');
  };

  vp.addEventListener('mousedown', (e) => { startDrag(e.clientX, e.clientY); });
  window.addEventListener('mousemove', (e) => moveDrag(e.clientX, e.clientY));
  window.addEventListener('mouseup', endDrag);
  vp.addEventListener('touchstart', (e) => {
    const t = e.touches[0]; if (t) startDrag(t.clientX, t.clientY);
  }, { passive: true });
  vp.addEventListener('touchmove', (e) => {
    const t = e.touches[0]; if (t) moveDrag(t.clientX, t.clientY);
  }, { passive: true });
  vp.addEventListener('touchend', endDrag);
})();

(function initResultViewportResizeObserver() {
  const vp = $('#result-viewport');
  if (!vp || vp.dataset.resizeObs || typeof ResizeObserver === 'undefined') return;
  vp.dataset.resizeObs = '1';
  let deb;
  const ro = new ResizeObserver(() => {
    clearTimeout(deb);
    deb = setTimeout(() => {
      const ir = $('#inspect-result');
      if (state.currentInspection && ir && !ir.hidden) redrawResultDefectOverlay();
    }, 80);
  });
  ro.observe(vp);
})();

$('#result-zoom-reset')?.addEventListener('click', resetResultView);
$('#result-zoom-range')?.addEventListener('input', (ev) => {
  setResultZoom(parseFloat(ev.target.value));
});
document.querySelectorAll('[data-result-zoom]').forEach((btn) => {
  btn.addEventListener('click', () => {
    const step = btn.dataset.resultZoom === 'in' ? 0.35 : -0.35;
    setResultZoom(state.resultView.zoom + step);
  });
});

async function downloadWithAuth(ev, url, filename) {
  ev.preventDefault();
  const res = await api(url, { raw: true });
  if (!res.ok) {
    let msg = `Ошибка ${res.status}`;
    try {
      const data = await res.json();
      msg = data?.detail || msg;
    } catch {}
    setStatus(msg, 'error');
    return;
  }
  const blob = await res.blob();
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = filename;
  a.click();
  URL.revokeObjectURL(a.href);
  setStatus(`Файл ${filename} скачан.`, 'success');
}

function reviewVerdictEntry(id) {
  const v = state.review.verdicts[id];
  if (v === undefined) return undefined;
  if (typeof v === 'boolean') return { real: v, excludeTraining: false };
  return { real: !!v.real, excludeTraining: !!v.excludeTraining };
}

function setReviewVerdict(id, real, excludeTraining = false) {
  state.review.verdicts[id] = {
    real: !!real,
    excludeTraining: !!excludeTraining && !real,
  };
}

// ---------- Ручная проверка дефектов ----------
$('#btn-review')?.addEventListener('click', () => {
  if (!state.currentInspection) return;
  const reset = !!state.currentInspection.reviewed_at;
  openReview(state.currentInspection, { reset, mode: 'defects' });
});

$('#btn-review-recognition')?.addEventListener('click', () => {
  if (!state.currentInspection) return;
  const reset = !!state.currentInspection.reviewed_at;
  openReview(state.currentInspection, { reset, mode: 'recognition' });
});

function openReview(inspection, { reset = false, mode = 'defects' } = {}) {
  if (!inspection?.defects?.length) return;
  const queue = mode === 'recognition'
    ? recognitionForReviewQueue(inspection.defects)
    : defectsForReviewQueue(inspection.defects);
  if (queue.length === 0) {
    if (mode === 'defects') {
      (async () => {
        try {
          const updated = await api(`/api/inspections/${inspection.id}/review`, {
            method: 'POST',
            body: { reviews: [] },
          });
          renderInspection(updated);
          setStatus('Дефектов для ручной оценки нет.', 'success');
        } catch (e) {
          alert(e.message);
        }
      })();
    } else {
      setStatus('Нет компонентов, требующих проверки распознавания.', 'warn');
    }
    return;
  }
  state.review.mode = mode;
  state.review.queueIds = queue.map((d) => d.id);
  state.review.inspection = inspection;
  if (reset) {
    state.review.verdicts = {};
    state.review.index = 0;
  } else {
    state.review.verdicts = {};
    queue.forEach((d) => {
      if (d.is_reviewed) {
        const real = !!d.is_real_defect;
        setReviewVerdict(d.id, real, !!(d.exclude_from_training) && !real);
      }
    });
    const idx = queue.findIndex((d) => reviewVerdictEntry(d.id) === undefined);
    state.review.index = idx >= 0 ? idx : 0;
  }
  resetReviewZoom();
  $('#review-error').textContent = '';
  $('#review-modal').hidden = false;
  showReviewDefect();
}

function closeReview() {
  $('#review-modal').hidden = true;
  state.review.inspection = null;
  state.review.queueIds = [];
  const fb = $('#review-feedback');
  if (fb) {
    fb.hidden = true;
    fb.textContent = '';
  }
  if (state.review.feedbackTimer) {
    clearTimeout(state.review.feedbackTimer);
    state.review.feedbackTimer = null;
  }
}

function showReviewDefect() {
  const rev = state.review;
  const insp = rev.inspection;
  if (!insp) return;
  const q = rev.queueIds;
  const total = q.length;
  const i = Math.max(0, Math.min(rev.index, total - 1));
  rev.index = i;
  const did = q[i];
  const d = insp.defects.find((x) => x.id === did);
  if (!d) return;

  const isRec = rev.mode === 'recognition';
  $('#review-title').textContent = isRec
    ? `Проверка распознавания · инспекция №${insp.id}`
    : `Оценка дефектов · инспекция №${insp.id}`;
  $('#review-progress-label').textContent = `${i + 1} / ${total}`;
  const doneCount = q.filter((id) => reviewVerdictEntry(id) !== undefined).length;
  $('#review-progress-fill').style.width = `${total ? (doneCount / total) * 100 : 0}%`;

  $('#review-class').textContent = `${i + 1}. ${d.class_name}`;
  $('#review-class-code').textContent = `(${d.class_code})`;
  $('#review-confidence').textContent = d.confidence.toFixed(3);
  $('#review-bbox').textContent = `x: ${d.bbox_x1}–${d.bbox_x2}, y: ${d.bbox_y1}–${d.bbox_y2} (${d.bbox_x2 - d.bbox_x1}×${d.bbox_y2 - d.bbox_y1})`;

  const clearOutline = (el) => { if (el) el.style.outline = ''; };
  const acc = $('#review-accept');
  const rej = $('#review-reject');
  const rejWrong = $('#review-reject-wrong-class');
  if (acc) acc.textContent = isRec ? 'Верно' : 'Брак';
  if (rej) rej.textContent = isRec ? 'Лишнее' : 'Не брак';
  if (rejWrong) rejWrong.textContent = 'Ошибка распознавания';
  [acc, rej, rejWrong].forEach(clearOutline);
  const entry = reviewVerdictEntry(d.id);
  if (entry) {
    if (isRec) {
      if (!entry.real && !entry.excludeTraining) acc.style.outline = '3px solid #065f46';
      else if (entry.excludeTraining && rejWrong) rejWrong.style.outline = '3px solid #7f1d1d';
      else rej.style.outline = '3px solid #7f1d1d';
    } else if (entry.real) acc.style.outline = '3px solid #065f46';
    else if (entry.excludeTraining && rejWrong) rejWrong.style.outline = '3px solid #7f1d1d';
    else rej.style.outline = '3px solid #7f1d1d';
  }

  // Кнопка «Сохранить» активна, когда выставлены оценки по очереди.
  const submit = $('#review-submit');
  submit.hidden = doneCount < total;

  // Навигация.
  $('#review-prev').disabled = i === 0;
  $('#review-skip').disabled = i >= total - 1;

  // Грузим кроп по защищённому URL.
  const img = $('#review-image');
  img.removeAttribute('src');
  resetReviewZoom();
  img.onload = () => {
    state.review.imageSize = { w: img.naturalWidth || 0, h: img.naturalHeight || 0 };
    applyReviewTransform();
  };
  loadImageWithAuth(`/api/inspections/${insp.id}/defects/${d.id}/crop?padding=40`, img)
    .catch((e) => { $('#review-error').textContent = 'Ошибка загрузки: ' + e.message; });
}

function resetReviewZoom() {
  state.review.zoom = 1.6;
  state.review.pan = { x: 0, y: 0 };
  const r = $('#review-zoom-range');
  if (r) r.value = '1.6';
  applyReviewTransform();
}

function applyReviewTransform() {
  const vp = $('#review-viewport');
  const img = $('#review-image');
  if (!vp || !img) return;
  vp.style.setProperty('--zoom', String(state.review.zoom));
  img.style.transform = `translate(calc(-50% + ${state.review.pan.x}px), calc(-50% + ${state.review.pan.y}px)) scale(${state.review.zoom})`;
}

function setReviewZoom(z) {
  state.review.zoom = Math.max(1, Math.min(8, z));
  const r = $('#review-zoom-range');
  if (r) r.value = String(state.review.zoom);
  applyReviewTransform();
}

function recordVerdict(isReal, excludeTraining = false, { recognitionOk = false } = {}) {
  const rev = state.review;
  const insp = rev.inspection;
  if (!insp) return;
  const q = rev.queueIds;
  const d = insp.defects.find((x) => x.id === q[rev.index]);
  if (!d) return;
  if (recognitionOk) {
    setReviewVerdict(d.id, false, false);
    showReviewFeedback(false, false, true);
  } else {
    setReviewVerdict(d.id, isReal, excludeTraining);
    showReviewFeedback(isReal, excludeTraining, false);
  }
  const nextPending = q.findIndex((id) => reviewVerdictEntry(id) === undefined);
  rev.index = nextPending >= 0 ? nextPending : q.length - 1;
  showReviewDefect();
}

function showReviewFeedback(isReal, excludeTraining = false, recognitionOk = false) {
  const fb = $('#review-feedback');
  if (!fb) return;
  fb.className = `status ${isReal || recognitionOk ? 'success' : 'warn'}`;
  let msg = 'Сохранено: Брак';
  if (recognitionOk) {
    msg = 'Сохранено: верно распознан';
  } else if (!isReal) {
    msg = excludeTraining
      ? 'Сохранено: ошибка распознавания (не в обучение)'
      : 'Сохранено: не брак';
  }
  fb.textContent = msg;
  fb.hidden = false;
  if (state.review.feedbackTimer) clearTimeout(state.review.feedbackTimer);
  state.review.feedbackTimer = setTimeout(() => {
    fb.hidden = true;
    fb.textContent = '';
    state.review.feedbackTimer = null;
  }, 900);
}

$('#review-accept')?.addEventListener('click', () => {
  if (state.review.mode === 'recognition') recordVerdict(false, false, { recognitionOk: true });
  else recordVerdict(true);
});
$('#review-reject')?.addEventListener('click', () => recordVerdict(false, false));
$('#review-reject-wrong-class')?.addEventListener('click', () => recordVerdict(false, true));
$('#review-prev')?.addEventListener('click', () => {
  if (state.review.index > 0) { state.review.index -= 1; showReviewDefect(); }
});
$('#review-skip')?.addEventListener('click', () => {
  const total = state.review.queueIds?.length || 0;
  if (state.review.index < total - 1) { state.review.index += 1; showReviewDefect(); }
});
$('#review-zoom-reset')?.addEventListener('click', resetReviewZoom);
$('#review-zoom-range')?.addEventListener('input', (ev) => {
  setReviewZoom(parseFloat(ev.target.value));
});
document.querySelectorAll('[data-zoom]').forEach((btn) => {
  btn.addEventListener('click', () => {
    const step = btn.dataset.zoom === 'in' ? 0.4 : -0.4;
    setReviewZoom(state.review.zoom + step);
  });
});

// Зум колесом мыши + перетаскивание.
(function initReviewViewport() {
  const vp = $('#review-viewport');
  if (!vp) return;
  vp.addEventListener('wheel', (ev) => {
    if ($('#review-modal').hidden) return;
    ev.preventDefault();
    const delta = -ev.deltaY * 0.0025;
    setReviewZoom(state.review.zoom * (1 + delta));
  }, { passive: false });

  const startDrag = (x, y) => {
    state.review.drag = { x, y, startX: state.review.pan.x, startY: state.review.pan.y };
    vp.classList.add('is-grabbing');
  };
  const moveDrag = (x, y) => {
    const d = state.review.drag;
    if (!d) return;
    state.review.pan = { x: d.startX + (x - d.x), y: d.startY + (y - d.y) };
    applyReviewTransform();
  };
  const endDrag = () => {
    state.review.drag = null;
    vp.classList.remove('is-grabbing');
  };

  vp.addEventListener('mousedown', (e) => { startDrag(e.clientX, e.clientY); });
  window.addEventListener('mousemove', (e) => moveDrag(e.clientX, e.clientY));
  window.addEventListener('mouseup', endDrag);
  vp.addEventListener('touchstart', (e) => {
    const t = e.touches[0]; if (t) startDrag(t.clientX, t.clientY);
  }, { passive: true });
  vp.addEventListener('touchmove', (e) => {
    const t = e.touches[0]; if (t) moveDrag(t.clientX, t.clientY);
  }, { passive: true });
  vp.addEventListener('touchend', endDrag);
})();

// Закрытие модалки.
document.addEventListener('click', (ev) => {
  const modal = $('#review-modal');
  if (!modal || modal.hidden) return;
  if (ev.target.closest('#review-modal .modal-backdrop') || ev.target.closest('#review-modal [data-close]')) {
    closeReview();
  }
});

$('#review-submit')?.addEventListener('click', async () => {
  const rev = state.review;
  const insp = rev.inspection;
  if (!insp || rev.submitting) return;
  const q = rev.queueIds || [];
  if (q.some((id) => reviewVerdictEntry(id) === undefined)) {
    $('#review-error').textContent = 'Оцените все позиции в очереди перед сохранением.';
    return;
  }
  const reviews = q.map((id) => {
    const { real, excludeTraining } = reviewVerdictEntry(id);
    return {
      defect_id: id,
      is_real_defect: real,
      exclude_from_training: excludeTraining,
    };
  });
  if (reviews.length === 0) return;
  rev.submitting = true;
  $('#review-error').textContent = 'Сохранение…';
  try {
    const updated = await api(`/api/inspections/${insp.id}/review`, {
      method: 'POST',
      body: { reviews },
    });
    closeReview();
    renderInspection(updated);
    setStatus(`Оценка сохранена. Подтверждено дефектов: ${updated.defects_count}.`, 'success');
  } catch (e) {
    $('#review-error').className = 'status error';
    $('#review-error').textContent = e.message;
  } finally {
    rev.submitting = false;
  }
});

// Горячие клавиши в модалке.
document.addEventListener('keydown', (ev) => {
  const modal = $('#review-modal');
  if (!modal || modal.hidden) return;
  if (ev.key === 'Escape') { closeReview(); return; }
  if (ev.target.matches('input, textarea, select')) return;
  if (ev.key === 'ArrowLeft')  { $('#review-prev').click(); }
  if (ev.key === 'ArrowRight') { $('#review-skip').click(); }
  if (ev.key === '1' || ev.key.toLowerCase() === 'd') { recordVerdict(true); }
  if (ev.key === '2' || ev.key.toLowerCase() === 'n') { recordVerdict(false); }
});

// ---------- Журнал ----------
$('#history-filter').addEventListener('submit', (ev) => { ev.preventDefault(); loadHistory(); });

async function loadHistory() {
  const fd = new FormData($('#history-filter'));
  const params = new URLSearchParams();
  const from = fd.get('from_date'); if (from) params.set('from_date', from + 'T00:00:00');
  const to = fd.get('to_date'); if (to) params.set('to_date', to + 'T23:59:59');
  const cls = fd.get('class_code'); if (cls) params.set('class_code', cls);
  const op = fd.get('operator_id'); if (op) params.set('operator_id', op);
  const dev = fd.get('device_id'); if (dev) params.set('device_id', dev);

  const body = $('#history-body');
  body.innerHTML = '<tr><td colspan="10" class="empty">Загрузка…</td></tr>';
  try {
    const dataRaw = await api(`/api/inspections?${params.toString()}`);
    state.historyLastRawIds = dataRaw.map((i) => i.id);
    const hidden = readOperatorJournalHiddenIds();
    const data =
      isElevatedRole()
        ? dataRaw
        : dataRaw.filter((i) => !hidden.has(i.id));
    updateHistoryUnhideButton();
    if (!data.length) {
      if (!dataRaw.length) {
        body.innerHTML = '<tr><td colspan="10" class="empty">Нет записей</td></tr>';
      } else {
        body.innerHTML =
          '<tr><td colspan="10" class="empty">Все записи в этом фильтре скрыты вашим браузером. Нажмите «Показать скрытые записи» или измените фильтр.</td></tr>';
      }
      return;
    }
    body.innerHTML = '';
    data.forEach((i) => {
      const tr = document.createElement('tr');
      tr.innerHTML = `
        <td>${i.id}</td>
        <td>${formatDate(i.created_at)}</td>
        <td>${i.operator_username ?? '—'}</td>
        <td>${i.device_name ?? '—'}</td>
        <td>${i.board_model || '—'}</td>
        <td>${i.original_filename}</td>
        <td>${i.defects_count}</td>
        <td>${i.avg_confidence ? i.avg_confidence.toFixed(3) : '—'}</td>
        <td>${i.inference_time_ms ? i.inference_time_ms.toFixed(0) + ' мс' : '—'}</td>
        <td><button class="btn btn--ghost" data-id="${i.id}" style="color:#1f4ed8;border-color:#93c5fd">Открыть</button></td>`;
      tr.querySelector('button').addEventListener('click', () => openInspection(i.id));
      body.appendChild(tr);
    });
  } catch (e) {
    state.historyLastRawIds = [];
    body.innerHTML = `<tr><td colspan="10" class="empty">Ошибка: ${e.message}</td></tr>`;
    updateHistoryUnhideButton();
  }
}

async function openInspection(id) {
  if (!isElevatedRole() && readOperatorJournalHiddenIds().has(Number(id))) {
    setStatus(
      'Эта инспекция скрыта в вашем журнале. Откройте «Показать скрытые записи» на вкладке «Журнал», затем выберите запись.',
      'error',
    );
    return;
  }
  revealInspectPanelForOperator();
  try {
    const data = await api(`/api/inspections/${id}`);
    showTab('tab-inspect');
    renderInspection(data);
    setStatus(`Загружена инспекция №${id}`, 'success');
  } catch (e) {
    setStatus(e.message, 'error');
  }
}

/** Журнал на сервере не меняется: для оператора id запоминаются — не появятся после F5 и нового входа. */
function clearHistoryViewVisual() {
  const body = $('#history-body');
  if (isElevatedRole()) {
    body.innerHTML =
      '<tr><td colspan="10" class="empty">Таблица очищена только на этом экране. «Обновить» — снова загрузить журнал с сервера.</td></tr>';
    return;
  }
  if (state.user) {
    const hidden = readOperatorJournalHiddenIds();
    (state.historyLastRawIds || []).forEach((x) => hidden.add(x));
    $$('#history-body tr').forEach((tr) => {
      const td0 = tr.querySelector('td');
      if (!td0) return;
      const nid = Number(td0.textContent.trim());
      if (Number.isFinite(nid)) hidden.add(nid);
    });
    persistOperatorJournalHiddenIds(hidden);
  }
  body.innerHTML =
    '<tr><td colspan="10" class="empty">Записи скрыты в этом браузере (сохраняется после перезагрузки). Новые инспекции появятся, если их id ещё не в вашем локальном списке скрытых.</td></tr>';
  updateHistoryUnhideButton();
}

/** Карточка результата: для оператора не показывается снова до новой инспекции или «Показать скрытые». */
function clearInspectResultViewVisual() {
  closeReview();
  teardownInspectResultDomOnly();
  if (!isElevatedRole()) persistInspectPanelHidden(true);
  setStatus('', '');
}

const PURGE_INSPECTIONS_CONFIRM = 'DELETE_ALL_INSPECTIONS';

async function purgeAllInspectionsFromServer() {
  if (!isAdminRole()) return;
  const typed = window.prompt(
    `Необратимо удалит все инспекции, дефекты и файлы изображений на сервере.\n` +
      `Сводная статистика станет пустой.\n\nВведите точно: ${PURGE_INSPECTIONS_CONFIRM}`
  );
  if (typed !== PURGE_INSPECTIONS_CONFIRM) {
    if (typed !== null && typed !== '') alert('Фраза подтверждения не совпала — операция отменена.');
    return;
  }
  try {
    const out = await api('/api/inspections/admin/purge-all', {
      method: 'POST',
      body: { confirm: PURGE_INSPECTIONS_CONFIRM },
    });
    clearOperatorJournalHiddenIds();
    persistInspectPanelHidden(false);
    closeReview();
    teardownInspectResultDomOnly();
    setStatus(`На сервере удалено инспекций: ${out.deleted}`, 'success');
    await loadHistory();
    loadStats();
    updateHistoryUnhideButton();
  } catch (e) {
    alert(e.message);
  }
}

$('#btn-history-clear-view')?.addEventListener('click', () => clearHistoryViewVisual());
$('#btn-history-unhide-local')?.addEventListener('click', () => {
  clearOperatorJournalHiddenIds();
  loadHistory();
});
$('#btn-inspect-clear-view')?.addEventListener('click', () => clearInspectResultViewVisual());

$('#inspect-result')?.addEventListener('click', (ev) => {
  const btn = ev.target.closest('[data-defects-filter]');
  if (!btn || $('#inspect-result')?.hidden) return;
  setInspectDefectsListMode(btn.dataset.defectsFilter);
  if (state.currentInspection) {
    syncInspectDefectsFilterToolbar();
    renderDefectsList(state.currentInspection);
    redrawResultDefectOverlay();
  }
});
$('#btn-history-purge-server')?.addEventListener('click', () => purgeAllInspectionsFromServer());
$('#btn-stats-purge-server')?.addEventListener('click', () => purgeAllInspectionsFromServer());

// ---------- Статистика ----------
$('#stats-filter').addEventListener('submit', (ev) => { ev.preventDefault(); loadStats(); });

async function loadStats() {
  if (!isElevatedRole()) return;
  const fd = new FormData($('#stats-filter'));
  const params = new URLSearchParams();
  const from = fd.get('from_date'); if (from) params.set('from_date', from + 'T00:00:00');
  const to = fd.get('to_date'); if (to) params.set('to_date', to + 'T23:59:59');
  try {
    const s = await api(`/api/stats?${params.toString()}`);
    $('#kpi-total').textContent = s.total_inspections;
    $('#kpi-def').textContent = s.defective_inspections;
    $('#kpi-clean').textContent = s.clean_inspections;
    $('#kpi-defects').textContent = s.total_defects;

    const max = Math.max(...s.by_class.map((c) => c.count), 1);
    const bars = $('#stats-classes');
    bars.innerHTML = '';
    const colorMap = Object.fromEntries(state.defectClasses.map((c) => [c.code, c.color]));
    s.by_class.forEach((c) => {
      const row = document.createElement('div');
      row.className = 'bar-row';
      row.innerHTML = `
        <span>${c.class_name}</span>
        <span class="bar-track"><span class="bar-fill" style="width:${(c.count / max) * 100}%;background:${colorMap[c.class_code] || '#1f4ed8'}"></span></span>
        <span>${c.count}</span>`;
      bars.appendChild(row);
    });
    if (!s.by_class.length) bars.innerHTML = '<p class="hint">Нет данных</p>';

    const ops = $('#stats-operators');
    ops.innerHTML = '';
    s.by_operator.forEach((o) => {
      const tr = document.createElement('tr');
      tr.innerHTML = `<td>${o.username}</td><td>${o.full_name || '—'}</td><td>${o.inspections_count}</td><td>${o.defects_count}</td>`;
      ops.appendChild(tr);
    });
    if (!s.by_operator.length) ops.innerHTML = '<tr><td colspan="4" class="empty">Нет данных</td></tr>';

    renderDailyChart(s.by_day || []);
    renderWeekdayChart(s.by_weekday || []);
  } catch (e) {
    setStatus(e.message, 'error');
  }
}

// --- SVG-графики для статистики (без внешних зависимостей) ---

function escapeXML(s) {
  return String(s).replace(/[<>&'"]/g, (c) => ({ '<':'&lt;', '>':'&gt;', '&':'&amp;', "'":'&apos;', '"':'&quot;' })[c]);
}

function renderDailyChart(data) {
  const host = $('#chart-daily');
  if (!host) return;
  if (!data.length) { host.innerHTML = '<p class="hint">Нет данных за выбранный период</p>'; return; }

  const W = Math.max(600, Math.min(1200, data.length * 28 + 80));
  const H = 260;
  const padL = 40, padR = 12, padT = 14, padB = 44;
  const plotW = W - padL - padR, plotH = H - padT - padB;
  const maxV = Math.max(1, ...data.flatMap((d) => [d.inspections, d.defects]));

  const bw = plotW / data.length;
  const barsHtml = data.map((d, i) => {
    const x = padL + i * bw;
    const hInsp = (d.inspections / maxV) * plotH;
    const hDef  = (d.defects / maxV) * plotH;
    const w = Math.max(2, (bw - 4) / 2);
    return `
      <rect x="${x + 2}"     y="${padT + plotH - hInsp}" width="${w}" height="${hInsp}" fill="#1f4ed8" rx="1">
        <title>${escapeXML(d.date)}: инспекций ${d.inspections}</title>
      </rect>
      <rect x="${x + 2 + w + 2}" y="${padT + plotH - hDef}"  width="${w}" height="${hDef}"  fill="#ef4444" rx="1">
        <title>${escapeXML(d.date)}: дефектов ${d.defects}</title>
      </rect>`;
  }).join('');

  // Подписи оси X — реже при большом количестве дней, чтобы не налезали.
  const step = Math.ceil(data.length / 14);
  const xLabels = data.map((d, i) => {
    if (i % step !== 0 && i !== data.length - 1) return '';
    const x = padL + i * bw + bw / 2;
    const short = d.date.slice(5);
    return `<text x="${x}" y="${H - 22}" text-anchor="middle" font-size="10" fill="#4b5563" transform="rotate(-45 ${x} ${H - 22})">${short}</text>`;
  }).join('');

  const ticks = 4;
  const yLabels = Array.from({ length: ticks + 1 }, (_, i) => {
    const v = Math.round((maxV * i) / ticks);
    const y = padT + plotH - (i / ticks) * plotH;
    return `
      <line x1="${padL}" y1="${y}" x2="${padL + plotW}" y2="${y}" stroke="#e5e7eb" />
      <text x="${padL - 6}" y="${y + 3}" text-anchor="end" font-size="10" fill="#6b7280">${v}</text>`;
  }).join('');

  host.innerHTML = `
    <svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="xMinYMid meet">
      ${yLabels}
      ${barsHtml}
      ${xLabels}
    </svg>
    <div class="chart-legend">
      <span><span class="swatch" style="background:#1f4ed8"></span>Инспекции</span>
      <span><span class="swatch" style="background:#ef4444"></span>Дефекты</span>
    </div>`;
}

function renderWeekdayChart(data) {
  const host = $('#chart-weekday');
  if (!host) return;
  if (!data.length) { host.innerHTML = '<p class="hint">Нет данных</p>'; return; }

  const W = 720, H = 220;
  const padL = 40, padR = 12, padT = 14, padB = 40;
  const plotW = W - padL - padR, plotH = H - padT - padB;
  const maxV = Math.max(1, ...data.flatMap((d) => [d.inspections, d.defects]));
  const bw = plotW / data.length;

  const bars = data.map((d, i) => {
    const x = padL + i * bw;
    const hInsp = (d.inspections / maxV) * plotH;
    const hDef  = (d.defects / maxV) * plotH;
    const w = (bw - 12) / 2;
    return `
      <rect x="${x + 6}"         y="${padT + plotH - hInsp}" width="${w}" height="${hInsp}" fill="#1f4ed8" rx="2">
        <title>${d.weekday_name}: инспекций ${d.inspections}</title>
      </rect>
      <rect x="${x + 6 + w + 2}" y="${padT + plotH - hDef}"  width="${w}" height="${hDef}"  fill="#ef4444" rx="2">
        <title>${d.weekday_name}: дефектов ${d.defects}</title>
      </rect>
      <text x="${x + bw / 2}" y="${H - 14}" text-anchor="middle" font-size="12" fill="#374151">${d.weekday_name}</text>`;
  }).join('');

  const ticks = 4;
  const yLabels = Array.from({ length: ticks + 1 }, (_, i) => {
    const v = Math.round((maxV * i) / ticks);
    const y = padT + plotH - (i / ticks) * plotH;
    return `
      <line x1="${padL}" y1="${y}" x2="${padL + plotW}" y2="${y}" stroke="#e5e7eb" />
      <text x="${padL - 6}" y="${y + 3}" text-anchor="end" font-size="10" fill="#6b7280">${v}</text>`;
  }).join('');

  host.innerHTML = `
    <svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="xMinYMid meet">
      ${yLabels}
      ${bars}
    </svg>
    <div class="chart-legend">
      <span><span class="swatch" style="background:#1f4ed8"></span>Инспекции</span>
      <span><span class="swatch" style="background:#ef4444"></span>Дефекты</span>
    </div>`;
}

// --- Быстрый период + экспорт Excel ---
$('#stats-quick')?.addEventListener('change', (ev) => {
  const key = ev.target.value;
  if (!key) return;
  const form = $('#stats-filter');
  const now = new Date();
  const pad = (n) => String(n).padStart(2, '0');
  const iso = (d) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
  let from, to = new Date(now);
  if (key === 'month') { from = new Date(now.getFullYear(), now.getMonth(), 1); }
  else if (key === 'year') { from = new Date(now.getFullYear(), 0, 1); }
  else {
    const days = parseInt(key, 10);
    from = new Date(now); from.setDate(now.getDate() - days + 1);
  }
  form.elements.from_date.value = iso(from);
  form.elements.to_date.value = iso(to);
  loadStats();
});

$('#btn-stats-xlsx')?.addEventListener('click', async () => {
  const fd = new FormData($('#stats-filter'));
  const params = new URLSearchParams();
  const from = fd.get('from_date'); if (from) params.set('from_date', from + 'T00:00:00');
  const to = fd.get('to_date'); if (to) params.set('to_date', to + 'T23:59:59');
  const url = `/api/stats/export.xlsx?${params.toString()}`;
  try {
    const res = await api(url, { raw: true });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const blob = await res.blob();
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = `aoi_stats_${(from||'all')}-${(to||'all')}.xlsx`;
    a.click();
    URL.revokeObjectURL(a.href);
  } catch (e) {
    alert('Не удалось скачать Excel: ' + e.message);
  }
});

// ---------- Пользователи ----------
$('#user-form').addEventListener('submit', async (ev) => {
  ev.preventDefault();
  if (!isAdminRole()) return;
  const fd = new FormData(ev.target);
  const msg = $('#user-msg');
  msg.className = 'status';
  try {
    await api('/api/users', {
      method: 'POST',
      body: {
        username: fd.get('username'),
        full_name: fd.get('full_name') || '',
        password: fd.get('password'),
        role: fd.get('role'),
      },
    });
    msg.classList.add('success');
    msg.textContent = 'Пользователь создан';
    ev.target.reset();
    await loadUsers();
  } catch (e) {
    msg.classList.add('error');
    msg.textContent = e.message;
  }
});

async function loadUsers() {
  if (!isElevatedRole()) return;
  const body = $('#users-body');
  try {
    const data = await api('/api/users');
    state.users = data;
    body.innerHTML = '';
    data.forEach((u) => {
      const tr = document.createElement('tr');
      const selfDel = u.id === state.currentUserId;
      const blockBtn = canManageUserBlock(u)
        ? (userIsBlocked(u)
          ? `<button class="btn btn--ghost" data-act="unblock" data-id="${u.id}" style="color:#111">Снять блок</button>`
          : `<button class="btn btn--ghost" data-act="block" data-id="${u.id}" style="color:#111">Блок.</button>`)
        : '';
      const deleteBtn = isAdminRole() && !selfDel
        ? `<button class="btn btn--danger" data-act="delete" data-id="${u.id}">Удалить</button>`
        : '';
      const pwdBtn = isAdminRole() && !selfDel
        ? `<button class="btn btn--ghost" data-act="password" data-id="${u.id}" style="color:#111">Пароль</button>`
        : '';
      const actions = (blockBtn || pwdBtn || deleteBtn) ? `${blockBtn}${pwdBtn}${deleteBtn}` : '—';
      tr.innerHTML = `
        <td>${u.username}</td>
        <td>${u.full_name || '—'}</td>
        <td>${roleRu(u.role)}</td>
        <td>${userIsBlocked(u) ? 'Нет' : (u.is_active ? 'Да' : 'Нет')}</td>
        <td>${u.locked_until ? formatDate(u.locked_until) : '—'}</td>
        <td>${actions}</td>`;
      tr.querySelectorAll('button').forEach((b) =>
        b.addEventListener('click', () => userAction(b.dataset.act, b.dataset.id))
      );
      body.appendChild(tr);
    });
    const sel = $('#history-filter select[name="operator_id"]');
    if (sel) {
      sel.innerHTML = '<option value="">— все —</option>';
      data.forEach((u) => {
        const opt = document.createElement('option');
        opt.value = u.id;
        opt.textContent = `${u.username}${u.full_name ? ' — ' + u.full_name : ''}`;
        sel.appendChild(opt);
      });
    }
  } catch (e) {
    body.innerHTML = `<tr><td colspan="6" class="empty">Ошибка: ${e.message}</td></tr>`;
  }
}

async function userAction(action, id) {
  try {
    if (action === 'block') {
      if (!confirm('Заблокировать пользователя? Вход в систему будет запрещён.')) return;
      await api(`/api/users/${id}`, { method: 'PATCH', body: { is_active: false } });
    } else if (action === 'unblock') {
      await api(`/api/users/${id}/unlock`, { method: 'POST' });
    } else if (action === 'delete') {
      if (!confirm('Удалить пользователя безвозвратно? Связанные инспекции будут удалены каскадом.')) return;
      await api(`/api/users/${id}`, { method: 'DELETE' });
    } else if (action === 'password') {
      const p = window.prompt('Новый пароль (не короче 8 символов):');
      if (p == null) return;
      if (p.length < 8) {
        alert('Пароль должен быть не короче 8 символов.');
        return;
      }
      await api(`/api/users/${id}/password`, { method: 'POST', body: { new_password: p } });
      alert('Пароль обновлён.');
    }
    await loadUsers();
  } catch (e) {
    alert(e.message);
  }
}

// ---------- Устройства ----------
$('#device-form')?.addEventListener('submit', async (ev) => {
  ev.preventDefault();
  const fd = new FormData(ev.target);
  const msg = $('#device-msg');
  msg.className = 'status';
  try {
    const res = await api('/api/devices', {
      method: 'POST',
      body: {
        name: fd.get('name'),
        description: fd.get('description') || null,
      },
    });
    msg.classList.add('success');
    msg.textContent = `Камера «${res.name}» создана. Ниже — ссылка и QR-код для телефона.`;
    ev.target.reset();
    openLinkModal(res);
  } catch (e) {
    msg.classList.add('error');
    msg.textContent = e.message;
  }
});

function closeRegistryEventSource() {
  if (state.registryEventSource) {
    try {
      state.registryEventSource.close();
    } catch (_) {
      /* ignore */
    }
    state.registryEventSource = null;
  }
}

/** Сервер push: список устройств + «моё» устройство (без опроса GET). */
function applyRegistryPayload(d) {
  if (!d || d.type !== 'registry') return;
  const devices = Array.isArray(d.devices) ? d.devices : [];
  state.devicesListCache = devices;
  const prevId = state.currentDevice?.id ?? null;
  state.currentDevice = d.mine ?? null;
  updateDeviceBadge();
  renderDevicesTable(devices);
  const nextId = state.currentDevice?.id ?? null;
  if (prevId !== nextId && $('#source-remote')?.classList.contains('is-active')) {
    clearTimeout(refreshRemoteSyncTimer);
    refreshRemoteSyncTimer = setTimeout(() => {
      refreshRemoteSyncTimer = null;
      syncRemoteView();
    }, 80);
  }
}

function startRegistryEventSource() {
  closeRegistryEventSource();
  if (!state.token) return;
  const url = `/api/devices/registry-events?token=${encodeURIComponent(state.token)}`;
  let es;
  try {
    es = new EventSource(url);
  } catch (_) {
    return;
  }
  state.registryEventSource = es;
  es.onmessage = (ev) => {
    try {
      const d = JSON.parse(ev.data);
      applyRegistryPayload(d);
    } catch (_) {
      /* ignore */
    }
  };
}

function updateDeviceListHint() {
  const el = $('#device-list-hint');
  if (!el) return;
  if (isElevatedRole()) {
    el.textContent = 'Закрепите камеру за сотрудником в колонке «Закреплён за» — тогда только он увидит её в списке. «Занято» — кто сейчас использует камеру для инспекции. Удаление — только администратор.';
  } else {
    el.textContent = 'Здесь только камеры, закреплённые за вами. Чтобы начать инспекцию, нажмите «Взять в работу».';
  }
}

async function ensureDeviceOperatorOptions() {
  if (state.deviceOperatorOptions?.length || !isElevatedRole()) return;
  try {
    const users = await api('/api/users');
    state.deviceOperatorOptions = users
      .filter((u) => u.role === 'operator')
      .sort((a, b) => a.username.localeCompare(b.username, 'ru'));
  } catch {
    state.deviceOperatorOptions = [];
  }
}

function buildDeviceDesignateSelect(device) {
  const sel = document.createElement('select');
  sel.className = 'device-designate-select';
  sel.title = 'Закрепить камеру за сотрудником';
  const empty = document.createElement('option');
  empty.value = '';
  empty.textContent = '— не закреплено —';
  sel.appendChild(empty);
  (state.deviceOperatorOptions || []).forEach((u) => {
    const opt = document.createElement('option');
    opt.value = String(u.id);
    opt.textContent = `${u.username}${u.full_name ? ` — ${u.full_name}` : ''}`;
    sel.appendChild(opt);
  });
  sel.value = device.designated_operator_id ? String(device.designated_operator_id) : '';
  sel.addEventListener('change', async () => {
    const msg = $('#device-msg');
    if (msg) { msg.textContent = ''; msg.className = 'status'; }
    try {
      await api(`/api/devices/${device.id}`, {
        method: 'PATCH',
        body: { designated_operator_id: sel.value ? Number(sel.value) : null },
      });
      if (msg) {
        msg.classList.add('success');
        msg.textContent = sel.value ? 'Камера закреплена за сотрудником' : 'Закрепление снято';
      }
      await loadDevices();
    } catch (e) {
      if (msg) {
        msg.classList.add('error');
        msg.textContent = e.message;
      }
      sel.value = device.designated_operator_id ? String(device.designated_operator_id) : '';
    }
  });
  return sel;
}

function renderDevicesTable(data) {
  const body = $('#devices-body');
  if (!body) return;
  updateDeviceListHint();
  const colSpan = isElevatedRole() ? 8 : 7;
  if (!data.length) {
    body.innerHTML = `<tr><td colspan="${colSpan}" class="empty">${isElevatedRole() ? 'Устройства не добавлены.' : 'Нет закреплённых камер. Обратитесь к руководителю.'}</td></tr>`;
  } else {
    body.innerHTML = '';
    data.forEach((d) => {
      const mine = d.assigned_operator_id && state.currentDevice?.id === d.id;
      const takenByOther = d.assigned_operator_id && !mine;
      const canTake = !d.assigned_operator_id && d.is_active;
      const actions = [];
      if (canTake) actions.push(`<button class="btn btn--primary" data-act="take" data-id="${d.id}">Взять в работу</button>`);
      if (mine) actions.push(`<button class="btn btn--secondary" data-act="release" data-id="${d.id}">Освободить</button>`);
      if (isElevatedRole()) {
        if (takenByOther) actions.push(`<button class="btn btn--danger" data-act="release" data-id="${d.id}">Снять закрепление</button>`);
        actions.push(`<button class="btn btn--ghost" data-act="link" data-id="${d.id}" style="color:#111">Ссылка/QR</button>`);
        actions.push(`<button class="btn btn--ghost" data-act="toggle" data-id="${d.id}" data-active="${d.is_active}" style="color:#111">${d.is_active ? 'Выкл.' : 'Вкл.'}</button>`);
        if (isAdminRole()) {
          actions.push(`<button class="btn btn--ghost" data-act="delete" data-id="${d.id}" style="color:#b91c1c">Удалить</button>`);
        }
      }
      const dot = d.is_streaming ? '<span class="dot dot-on"></span>В эфире' : '<span class="dot dot-off"></span>Не транслирует';
      const tr = document.createElement('tr');

      const tdName = document.createElement('td');
      tdName.textContent = d.name;
      tr.appendChild(tdName);

      const tdStatus = document.createElement('td');
      tdStatus.innerHTML = dot;
      tr.appendChild(tdStatus);

      if (isElevatedRole()) {
        const tdDes = document.createElement('td');
        tdDes.appendChild(buildDeviceDesignateSelect(d));
        tr.appendChild(tdDes);
      }

      const tdBusy = document.createElement('td');
      tdBusy.textContent = d.assigned_operator_username
        ? d.assigned_operator_username + (mine ? ' (вы)' : '')
        : '—';
      tr.appendChild(tdBusy);

      const tdSince = document.createElement('td');
      tdSince.textContent = d.assigned_at ? formatDate(d.assigned_at) : '—';
      tr.appendChild(tdSince);

      const tdReg = document.createElement('td');
      tdReg.textContent = d.registered_by_username || '—';
      tr.appendChild(tdReg);

      const tdActive = document.createElement('td');
      tdActive.textContent = d.is_active ? 'Да' : 'Нет';
      tr.appendChild(tdActive);

      const tdAct = document.createElement('td');
      tdAct.innerHTML = actions.join(' ');
      tdAct.querySelectorAll('button').forEach((b) =>
        b.addEventListener('click', () => deviceAction(b.dataset.act, b.dataset.id, b.dataset.active))
      );
      tr.appendChild(tdAct);

      body.appendChild(tr);
    });
  }
  const sel = $('#history-filter select[name="device_id"]');
  if (sel) {
    sel.innerHTML = '<option value="">— все —</option>';
    data.forEach((d) => {
      const opt = document.createElement('option');
      opt.value = d.id;
      opt.textContent = d.name;
      sel.appendChild(opt);
    });
  }
}

async function loadDevices() {
  const body = $('#devices-body');
  const colSpan = isElevatedRole() ? 8 : 7;
  body.innerHTML = `<tr><td colspan="${colSpan}" class="empty">Загрузка…</td></tr>`;
  try {
    await ensureDeviceOperatorOptions();
    const data = await api('/api/devices');
    state.devicesListCache = data;
    renderDevicesTable(data);
  } catch (e) {
    body.innerHTML = `<tr><td colspan="${colSpan}" class="empty">Ошибка: ${e.message}</td></tr>`;
  }
}

async function deviceAction(action, id, active) {
  const msg = $('#device-msg');
  msg.className = 'status';
  try {
    if (action === 'take') {
      const res = await api(`/api/devices/${id}/take`, { method: 'POST' });
      state.currentDevice = res;
      msg.classList.add('success');
      msg.textContent = `Устройство «${res.name}» взято в работу.`;
    } else if (action === 'release') {
      const res = await api(`/api/devices/${id}/release`, { method: 'POST' });
      if (state.currentDevice?.id === Number(id)) state.currentDevice = null;
      msg.classList.add('success');
      msg.textContent = `Устройство «${res.name}» освобождено.`;
    } else if (action === 'toggle') {
      await api(`/api/devices/${id}`, { method: 'PATCH', body: { is_active: !(active === 'true') } });
    } else if (action === 'delete') {
      if (!confirm('Удалить устройство?')) return;
      await api(`/api/devices/${id}`, { method: 'DELETE' });
    } else if (action === 'link') {
      const res = await api(`/api/devices/${id}/link`);
      openLinkModal(res);
    }
    updateDeviceBadge();
    if (action === 'take' || action === 'release') syncRemoteView();
  } catch (e) {
    msg.classList.add('error');
    msg.textContent = e.message;
  }
}

async function refreshMyDevice() {
  const prevId = state.currentDevice?.id ?? null;
  try {
    state.currentDevice = await api('/api/devices/mine');
  } catch {
    state.currentDevice = null;
  }
  updateDeviceBadge();
  const nextId = state.currentDevice?.id ?? null;
  if (prevId !== nextId && $('#source-remote')?.classList.contains('is-active')) {
    clearTimeout(refreshRemoteSyncTimer);
    refreshRemoteSyncTimer = setTimeout(() => {
      refreshRemoteSyncTimer = null;
      syncRemoteView();
    }, 80);
  }
}

// ---------- Модалка со ссылкой/QR ----------
function isLoopbackHost(hostname) {
  return /^(localhost|127\.0\.0\.1)$/i.test(hostname || '');
}

function openLinkModal(device) {
  const modal = $('#link-modal');
  if (!modal) return;
  const pub = (state.meta.public_base_url || '').trim().replace(/\/$/, '');
  const loopbackPage = isLoopbackHost(window.location.hostname);
  let url = (device.streaming_link || '').trim();
  if (!url) {
    const base = pub || (loopbackPage ? '' : location.origin.replace(/\/$/, ''));
    if (!base) {
      $('#link-msg').className = 'status error';
      $('#link-msg').textContent =
        'Не задан адрес для телефона. Откройте АОИ по IP в LAN (см. консоль exe) или задайте PUBLIC_BASE_URL.';
      url = '';
    } else {
      url = `${base}/phone?device=${device.id}&token=${encodeURIComponent(device.upload_token || '')}`;
    }
  }
  const hintLan = $('#link-hint-lan');
  if (hintLan) {
    let urlHost = '';
    try {
      urlHost = new URL(url).hostname;
    } catch {
      urlHost = '';
    }
    hintLan.hidden = !url || !isLoopbackHost(urlHost);
  }
  $('#link-title').textContent = `Ссылка для «${device.name}»`;
  $('#link-url').value = url;
  $('#link-open').href = url;
  // QR через публичный сервис (если нет интернета — покажем заглушку).
  const qrWrap = $('#link-qr');
  qrWrap.innerHTML = '';
  const qr = document.createElement('img');
  qr.alt = 'QR';
  qr.src = `https://api.qrserver.com/v1/create-qr-code/?size=300x300&margin=10&data=${encodeURIComponent(url)}`;
  qr.onerror = () => {
    qrWrap.innerHTML = '<span class="hint">QR недоступен: скопируйте ссылку вручную.</span>';
  };
  qrWrap.appendChild(qr);
  modal.hidden = false;
  modal.dataset.deviceId = String(device.id);
  $('#link-msg').textContent = '';
}

function closeLinkModal() {
  const modal = $('#link-modal');
  if (modal) modal.hidden = true;
}

document.addEventListener('click', (ev) => {
  const modal = $('#link-modal');
  if (!modal || modal.hidden) return;
  if (ev.target.closest('#link-modal .modal-backdrop') || ev.target.closest('#link-modal [data-close]')) {
    closeLinkModal();
  }
});

$('#link-copy')?.addEventListener('click', async () => {
  const url = $('#link-url').value;
  try {
    await navigator.clipboard.writeText(url);
    $('#link-msg').className = 'status success';
    $('#link-msg').textContent = 'Скопировано';
  } catch {
    $('#link-url').select();
    document.execCommand('copy');
    $('#link-msg').className = 'status success';
    $('#link-msg').textContent = 'Скопировано';
  }
});

$('#link-regen')?.addEventListener('click', async () => {
  const id = $('#link-modal').dataset.deviceId;
  if (!id) return;
  if (!confirm('Сгенерировать новый токен? Старый перестанет работать.')) return;
  try {
    const res = await api(`/api/devices/${id}/regenerate-token`, { method: 'POST' });
    openLinkModal(res);
  } catch (e) {
    $('#link-msg').className = 'status error';
    $('#link-msg').textContent = e.message;
  }
});

// ---------- Датасеты ----------
$('#dataset-form')?.addEventListener('submit', async (ev) => {
  ev.preventDefault();
  const fd = new FormData(ev.target);
  const msg = $('#dataset-msg');
  msg.className = 'status';
  msg.textContent = 'Загрузка файла…';
  try {
    // multipart через fetch с FormData — удобно для UploadFile.
    const file = fd.get('file');
    if (!file || !file.size) throw new Error('Файл не выбран');
    const body = new FormData();
    body.append('file', file, file.name);
    body.append('name', fd.get('name'));
    body.append('description', fd.get('description') || '');
    body.append('activate', fd.get('activate') ? 'true' : 'false');
    await api('/api/datasets', { method: 'POST', body });
    msg.classList.add('success');
    msg.textContent = 'Датасет загружен';
    ev.target.reset();
    await loadDatasets();
  } catch (e) {
    msg.classList.add('error');
    msg.textContent = e.message;
  }
});

async function loadDatasets() {
  if (!isElevatedRole()) return;
  const body = $('#datasets-body');
  body.innerHTML = '<tr><td colspan="7" class="empty">Загрузка…</td></tr>';
  try {
    const data = await api('/api/datasets');
    if (!data.length) {
      body.innerHTML = '<tr><td colspan="7" class="empty">Датасеты не загружены.</td></tr>';
      return;
    }
    body.innerHTML = '';
    data.forEach((d) => {
      const tr = document.createElement('tr');
      const badge = d.is_active
        ? '<span class="badge-active">Активный</span>'
        : '<span class="badge-inactive">Неактивный</span>';
      const actions = [];
      if (!d.is_active) actions.push(`<button class="btn btn--primary" data-act="activate" data-id="${d.id}">Сделать основным</button>`);
      if (d.is_active) {
        actions.push(
          `<button class="btn btn--secondary" data-act="deactivate" data-id="${d.id}" title="Вернуть детектор к весам по умолчанию из конфигурации">Отключить</button>`
        );
      }
      actions.push(`<button class="btn btn--ghost" data-act="delete" data-id="${d.id}" style="color:#b91c1c">Удалить</button>`);
      tr.innerHTML = `
        <td><div><b>${d.name}</b></div>${d.description ? `<small class="hint">${d.description}</small>` : ''}</td>
        <td>${formatBytes(d.file_size)}</td>
        <td>${d.original_filename || '—'}</td>
        <td>${formatDate(d.created_at)}</td>
        <td>${d.uploaded_by_username || '—'}</td>
        <td>${badge}</td>
        <td>${actions.join(' ')}</td>`;
      tr.querySelectorAll('button').forEach((b) =>
        b.addEventListener('click', () => datasetAction(b.dataset.act, b.dataset.id))
      );
      body.appendChild(tr);
    });
  } catch (e) {
    body.innerHTML = `<tr><td colspan="7" class="empty">Ошибка: ${e.message}</td></tr>`;
  }
}

async function datasetAction(action, id) {
  const msg = $('#dataset-msg');
  msg.className = 'status';
  try {
    if (action === 'activate') {
      await api(`/api/datasets/${id}/activate`, { method: 'POST' });
      msg.classList.add('success');
      msg.textContent = 'Датасет активирован, детектор перезагружен.';
    } else if (action === 'deactivate') {
      if (!confirm('Отключить активный датасет? Детектор вернётся к весам по умолчанию из конфигурации. Файл датасета останется в списке.')) return;
      await api('/api/datasets/deactivate', { method: 'POST' });
      msg.classList.add('success');
      msg.textContent = 'Активный датасет отключён, используются веса по умолчанию.';
    } else if (action === 'delete') {
      if (!confirm('Удалить датасет? Файл весов будет удалён с диска.')) return;
      await api(`/api/datasets/${id}`, { method: 'DELETE' });
      msg.classList.add('success');
      msg.textContent = 'Датасет удалён.';
    }
    await loadDatasets();
    await ensureDefectClassesLoaded(true);
  } catch (e) {
    msg.classList.add('error');
    msg.textContent = e.message;
  }
}

// ---------- Журнал аудита (руководитель и администратор) ----------
const AUDIT_USER_ROLE_GROUPS = [
  { role: 'admin', label: 'Администраторы' },
  { role: 'manager', label: 'Руководители' },
  { role: 'operator', label: 'Сотрудники' },
];
const AUDIT_ACTION_RU = {
  login_success: 'Вход в систему',
  login_failed: 'Неудачный вход',
  login_denied_locked: 'Вход заблокирован',
  logout: 'Выход',
  user_create: 'Создание пользователя',
  user_update: 'Изменение пользователя',
  user_unlock: 'Снятие блокировки',
  user_delete: 'Удаление пользователя',
  user_password_set: 'Смена пароля (админ)',
  password_change: 'Смена своего пароля',
  inspection_create: 'Инспекция',
  inspection_failed: 'Ошибка инспекции',
  inspection_delete: 'Удаление инспекции',
  inspection_purge_all: 'Очистка журнала инспекций',
  inspection_review: 'Оценка инспекции',
  device_create: 'Создание камеры',
  device_update: 'Изменение камеры',
  device_delete: 'Удаление камеры',
  device_take: 'Взял устройство',
  device_release: 'Освободил устройство',
  device_regenerate_token: 'Новая ссылка камеры',
  device_command: 'Команда камере',
  dataset_upload: 'Загрузка датасета',
  dataset_activate: 'Активация датасета',
  dataset_deactivate_all: 'Отключение датасета',
  dataset_delete: 'Удаление датасета',
  settings_update: 'Настройки системы',
  class_semantics_update: 'Классы модели',
  golden_board_create: 'Создание эталона',
  golden_board_update: 'Изменение эталона',
  golden_board_reference_upload: 'Снимок эталона',
  golden_board_auto_markup: 'Авторазметка эталона',
  golden_board_markup_save: 'Сохранение разметки',
  golden_board_delete: 'Удаление эталона',
};

const AUDIT_ACTION_OPTIONS = Object.keys(AUDIT_ACTION_RU).sort();

function auditActionLabel(code) {
  return AUDIT_ACTION_RU[code] || code || '—';
}

function auditDefaultFromDate() {
  const d = new Date();
  d.setDate(d.getDate() - 7);
  return d.toISOString().slice(0, 10);
}

function auditQueryParams(page = 1) {
  const fd = new FormData($('#audit-filter'));
  const q = new URLSearchParams();
  q.set('page', String(page));
  q.set('page_size', '50');
  const from = fd.get('from_date');
  const to = fd.get('to_date');
  const uid = fd.get('user_id');
  const act = fd.get('action');
  if (from) q.set('from_date', from);
  if (to) q.set('to_date', to);
  if (uid) q.set('user_id', uid);
  if (act) q.set('action', act);
  return q;
}

function auditUserOptionLabel(u) {
  const role = roleRu(u.role);
  const name = u.full_name ? ` — ${u.full_name}` : '';
  return `${u.username}${name} · ${role}`;
}

function fillAuditUserSelect(userSel, users) {
  if (!userSel) return;
  userSel.innerHTML = '<option value="">— все —</option>';
  const sorted = [...users].sort((a, b) => a.username.localeCompare(b.username, 'ru'));
  const groups = isAdminRole()
    ? AUDIT_USER_ROLE_GROUPS
    : [{ role: 'operator', label: 'Сотрудники' }];
  groups.forEach(({ role, label }) => {
    const items = sorted.filter((u) => u.role === role);
    if (!items.length) return;
    const og = document.createElement('optgroup');
    og.label = label;
    items.forEach((u) => {
      const opt = document.createElement('option');
      opt.value = String(u.id);
      opt.textContent = auditUserOptionLabel(u);
      og.appendChild(opt);
    });
    userSel.appendChild(og);
  });
}

function updateAuditHint() {
  const el = $('#audit-hint');
  if (!el) return;
  el.textContent = isAdminRole()
    ? 'Администратор видит все записи. В списке пользователей они сгруппированы: администраторы, руководители, сотрудники. Технический лог сервера — в файле logs/aoi.log.'
    : 'Руководитель видит только действия сотрудников. Выберите сотрудника в списке или оставьте «— все —». Технический лог сервера — в файле logs/aoi.log.';
}

async function populateAuditFilters() {
  const form = $('#audit-filter');
  if (!form) return;
  updateAuditHint();
  const fromIn = form.querySelector('[name=from_date]');
  const toIn = form.querySelector('[name=to_date]');
  if (fromIn && !fromIn.value) fromIn.value = auditDefaultFromDate();
  if (toIn && !toIn.value) toIn.value = new Date().toISOString().slice(0, 10);

  const userSel = form.querySelector('[name=user_id]');
  if (userSel) {
    try {
      const users = await api('/api/users');
      fillAuditUserSelect(userSel, users);
    } catch {
      /* ignore */
    }
  }

  const actSel = form.querySelector('[name=action]');
  if (actSel && actSel.options.length <= 1) {
    AUDIT_ACTION_OPTIONS.forEach((code) => {
      const opt = document.createElement('option');
      opt.value = code;
      opt.textContent = auditActionLabel(code);
      actSel.appendChild(opt);
    });
  }
}

async function loadAuditLogs(page = 1) {
  if (!isElevatedRole()) return;
  const body = $('#audit-body');
  const summary = $('#audit-summary');
  const pager = $('#audit-pager');
  const msg = $('#audit-msg');
  if (!body) return;
  await populateAuditFilters();
  body.innerHTML = '<tr><td colspan="6" class="empty">Загрузка…</td></tr>';
  if (msg) { msg.textContent = ''; msg.className = 'status'; }
  try {
    const data = await api(`/api/audit?${auditQueryParams(page).toString()}`);
    state.auditPage = data.page;
    state.auditTotal = data.total;
    state.auditPageSize = data.page_size;
    if (!data.items.length) {
      body.innerHTML = '<tr><td colspan="6" class="empty">Записей не найдено</td></tr>';
    } else {
      body.innerHTML = '';
      data.items.forEach((row) => {
        const tr = document.createElement('tr');
        tr.innerHTML = `
          <td>${formatDate(row.created_at)}</td>
          <td>${row.username || '—'}</td>
          <td>${auditActionLabel(row.action)}</td>
          <td>${row.target || '—'}</td>
          <td>${row.details ? `<small>${row.details}</small>` : '—'}</td>
          <td>${row.ip_address || '—'}</td>`;
        body.appendChild(tr);
      });
    }
    const pages = Math.max(1, Math.ceil(data.total / data.page_size));
    if (summary) {
      summary.textContent = `Найдено записей: ${data.total}. Страница ${data.page} из ${pages}.`;
    }
    if (pager) {
      pager.hidden = data.total <= data.page_size;
      const info = $('#audit-page-info');
      if (info) info.textContent = `${data.page} / ${pages}`;
      const prev = $('#btn-audit-prev');
      const next = $('#btn-audit-next');
      if (prev) prev.disabled = data.page <= 1;
      if (next) next.disabled = data.page >= pages;
    }
  } catch (e) {
    body.innerHTML = `<tr><td colspan="6" class="empty">Ошибка: ${e.message}</td></tr>`;
    if (pager) pager.hidden = true;
    if (summary) summary.textContent = '';
  }
}

async function loadAuditTab() {
  if (!isElevatedRole()) return;
  await loadAuditLogs(state.auditPage || 1);
}

async function exportAuditCsv() {
  const msg = $('#audit-msg');
  if (msg) { msg.textContent = ''; msg.className = 'status'; }
  try {
    await populateAuditFilters();
    const q = auditQueryParams(1);
    q.delete('page');
    q.delete('page_size');
    const res = await fetch(`/api/audit/export.csv?${q.toString()}`, {
      headers: state.token ? { Authorization: `Bearer ${state.token}` } : {},
    });
    if (!res.ok) {
      const txt = await res.text();
      throw new Error(txt || `Ошибка ${res.status}`);
    }
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `audit-log-${new Date().toISOString().slice(0, 10)}.csv`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
    if (msg) {
      msg.classList.add('success');
      msg.textContent = 'Файл CSV сохранён';
    }
  } catch (e) {
    if (msg) {
      msg.classList.add('error');
      msg.textContent = e.message;
    }
  }
}

$('#audit-filter')?.addEventListener('submit', (ev) => {
  ev.preventDefault();
  state.auditPage = 1;
  loadAuditLogs(1);
});
$('#btn-audit-export')?.addEventListener('click', () => exportAuditCsv());
$('#btn-audit-prev')?.addEventListener('click', () => {
  if ((state.auditPage || 1) > 1) loadAuditLogs(state.auditPage - 1);
});
$('#btn-audit-next')?.addEventListener('click', () => {
  loadAuditLogs((state.auditPage || 1) + 1);
});

// ---------- Настройки ----------
const settingsForm = $('#settings-form');
if (settingsForm) {
  settingsForm.addEventListener('input', (ev) => {
    const map = {
      detection_conf_threshold: ['#s-conf-v', (v) => v.toFixed(2)],
      detection_iou_threshold: ['#s-iou-v', (v) => v.toFixed(2)],
      live_analysis_interval_ms: ['#s-int-v', (v) => Math.round(v)],
      live_analysis_max_side: ['#s-side-v', (v) => Math.round(v)],
    };
    const m = map[ev.target.name];
    if (m) $(m[0]).textContent = m[1](parseFloat(ev.target.value));
  });
  settingsForm.addEventListener('submit', async (ev) => {
    ev.preventDefault();
    const fd = new FormData(ev.target);
    const body = {
      detection_conf_threshold: parseFloat(fd.get('detection_conf_threshold')),
      detection_iou_threshold: parseFloat(fd.get('detection_iou_threshold')),
      live_analysis_interval_ms: parseInt(fd.get('live_analysis_interval_ms'), 10),
      live_analysis_max_side: parseInt(fd.get('live_analysis_max_side'), 10),
    };
    const msg = $('#settings-msg'); msg.className = 'status';
    try {
      const res = await api('/api/settings', { method: 'PUT', body });
      state.meta = { ...state.meta, ...res };
      msg.classList.add('success'); msg.textContent = 'Настройки сохранены';
    } catch (e) {
      msg.classList.add('error'); msg.textContent = e.message;
    }
  });
}

async function loadSettings() {
  if (!isAdminRole()) return;
  try {
    const s = await api('/api/settings');
    state.meta = { ...state.meta, ...s };
    const f = $('#settings-form');
    f.elements.detection_conf_threshold.value = s.detection_conf_threshold;
    f.elements.detection_iou_threshold.value = s.detection_iou_threshold;
    f.elements.live_analysis_interval_ms.value = s.live_analysis_interval_ms;
    f.elements.live_analysis_max_side.value = s.live_analysis_max_side;
    $('#s-conf-v').textContent = s.detection_conf_threshold.toFixed(2);
    $('#s-iou-v').textContent = s.detection_iou_threshold.toFixed(2);
    $('#s-int-v').textContent = s.live_analysis_interval_ms;
    $('#s-side-v').textContent = s.live_analysis_max_side;
    await loadClassSemantics();
  } catch (e) {
    $('#settings-msg').className = 'status error';
    $('#settings-msg').textContent = e.message;
  }
}

async function loadClassSemantics() {
  if (!isAdminRole()) return;
  const wrap = $('#class-semantics-wrap');
  if (!wrap) return;
  wrap.innerHTML = '<p class="hint">Загрузка классов…</p>';
  try {
    const data = await api('/api/settings/class-semantics');
    const tbl = document.createElement('table');
    tbl.className = 'table';
    tbl.innerHTML = `<thead><tr><th>Код</th><th>Имя (модель)</th><th>Тип</th><th>Подпись</th><th>Ручная оценка</th></tr></thead>`;
    const tb = document.createElement('tbody');
    (data.detector_classes || []).forEach((c) => {
      const m = data.mappings[c.code] || {};
      const kind = m.kind || 'defect';
      const tr = document.createElement('tr');
      tr.dataset.code = c.code;
      tr.innerHTML = `<td><code></code></td><td class="sem-name"></td><td class="sem-kind-cell"></td><td class="sem-label-cell"></td><td class="sem-rr-cell"></td>`;
      tr.querySelector('code').textContent = c.code;
      tr.querySelector('.sem-name').textContent = c.name || '';
      const sel = document.createElement('select');
      sel.className = 'sem-kind';
      [['defect', 'Дефект'], ['component', 'Компонент (SMD и т.д.)'], ['ignore', 'Игнор']].forEach(([v, t]) => {
        const o = document.createElement('option');
        o.value = v;
        o.textContent = t;
        if (v === kind) o.selected = true;
        sel.appendChild(o);
      });
      tr.querySelector('.sem-kind-cell').appendChild(sel);
      const inp = document.createElement('input');
      inp.type = 'text';
      inp.className = 'sem-label';
      inp.maxLength = 128;
      inp.placeholder = 'Напр.: SMD резистор';
      inp.value = m.label || '';
      tr.querySelector('.sem-label-cell').appendChild(inp);
      const cb = document.createElement('input');
      cb.type = 'checkbox';
      cb.className = 'sem-rr';
      cb.checked = m.review_required !== false;
      tr.querySelector('.sem-rr-cell').appendChild(cb);
      tb.appendChild(tr);
    });
    tbl.appendChild(tb);
    wrap.innerHTML = '';
    wrap.appendChild(tbl);
  } catch (e) {
    wrap.innerHTML = `<p class="status error">${e.message}</p>`;
  }
}

$('#btn-save-semantics')?.addEventListener('click', async () => {
  const msg = $('#semantics-msg');
  if (msg) { msg.className = 'status'; msg.textContent = ''; }
  const wrap = $('#class-semantics-wrap');
  if (!wrap) return;
  const mappings = {};
  wrap.querySelectorAll('tbody tr').forEach((tr) => {
    const code = tr.dataset.code;
    if (!code) return;
    const kind = tr.querySelector('.sem-kind')?.value || 'defect';
    const label = tr.querySelector('.sem-label')?.value || '';
    const review_required = !!tr.querySelector('.sem-rr')?.checked;
    mappings[code] = { kind, label, review_required };
  });
  try {
    const out = await api('/api/settings/class-semantics', { method: 'PUT', body: { mappings } });
    state.classSemantics = {};
    (out.mappings && typeof out.mappings === 'object' && Object.entries(out.mappings).forEach(([k, v]) => {
      state.classSemantics[k] = v;
    }));
    if (msg) {
      msg.classList.add('success');
      msg.textContent = 'Сохранено';
    }
  } catch (e) {
    if (msg) {
      msg.classList.add('error');
      msg.textContent = e.message;
    }
  }
});

// ---------- Эталоны Golden Board (только руководитель) ----------
function revokeGoldenBoardImageUrl() {
  const img = $('#golden-ref-img');
  if (img?.src?.startsWith('blob:')) {
    try {
      URL.revokeObjectURL(img.src);
    } catch {
      /* ignore */
    }
    img.removeAttribute('src');
  }
}

function goldenOffsetToNatural(canvas, ox, oy) {
  if (!canvas || !canvas.clientWidth) return [0, 0];
  const sx = canvas.width / canvas.clientWidth;
  const sy = canvas.height / canvas.clientHeight;
  return [Math.round(ox * sx), Math.round(oy * sy)];
}

const GOLDEN_CLASS_GROUP_LABELS = {
  defect: 'Дефекты платы',
  solder: 'Пайка',
  component: 'Компоненты / монтаж',
};

async function ensureDefectClassesLoaded(force = false) {
  if (!force && Array.isArray(state.defectClasses) && state.defectClasses.length > 0) {
    populateGoldenClassSelects();
    return;
  }
  try {
    const meta = await fetch('/api/meta', state.token ? { headers: { Authorization: `Bearer ${state.token}` } } : undefined)
      .then((r) => (r.ok ? r.json() : null));
    if (meta?.defect_classes) {
      state.defectClasses = meta.defect_classes;
      populateGoldenClassSelects();
      if (state.goldenBoard.selectedId) {
        renderGoldenRegionsList();
        redrawGoldenRegions();
      }
    }
  } catch {
    /* ignore */
  }
}

function goldenClassMeta(code) {
  if (!code) return null;
  const list = state.defectClasses || [];
  const low = String(code).toLowerCase();
  return list.find((c) => String(c.code).toLowerCase() === low) || null;
}

function goldenClassDisplayName(code) {
  if (!code) return 'любой объект';
  const m = goldenClassMeta(code);
  return m ? `${m.name} (${m.code})` : String(code);
}

function goldenClassStrokeColor(code, selected) {
  if (selected) return '#eab308';
  const m = goldenClassMeta(code);
  if (m?.color) return m.color;
  return code ? '#22c55e' : '#64748b';
}

function fillGoldenClassSelect(selectEl, selectedCode) {
  if (!selectEl) return;
  const rawPrev = selectedCode != null ? String(selectedCode) : (selectEl.value || '');
  selectEl.innerHTML = '';
  const empty = document.createElement('option');
  empty.value = '';
  empty.textContent = '— любой объект —';
  selectEl.appendChild(empty);

  const classes = state.defectClasses || [];
  if (!classes.length) {
    const miss = document.createElement('option');
    miss.value = '';
    miss.textContent = '(классы модели не загружены)';
    miss.disabled = true;
    selectEl.appendChild(miss);
    return;
  }

  const groups = new Map();
  classes.forEach((c) => {
    const cat = c.category || 'other';
    if (!groups.has(cat)) groups.set(cat, []);
    groups.get(cat).push(c);
  });

  const order = ['component', 'solder', 'defect', 'other'];
  order.forEach((cat) => {
    const items = groups.get(cat);
    if (!items?.length) return;
    const og = document.createElement('optgroup');
    og.label = GOLDEN_CLASS_GROUP_LABELS[cat] || cat;
    items.forEach((c) => {
      const opt = document.createElement('option');
      opt.value = c.code;
      opt.textContent = `${c.name} (${c.code})`;
      og.appendChild(opt);
    });
    selectEl.appendChild(og);
  });

  if (rawPrev && !goldenClassMeta(rawPrev)) {
    const legacy = document.createElement('option');
    legacy.value = rawPrev;
    legacy.textContent = `${rawPrev} (нет в текущей модели)`;
    selectEl.insertBefore(legacy, selectEl.children[1] || null);
    selectEl.value = rawPrev;
  } else if (rawPrev && goldenClassMeta(rawPrev)) {
    selectEl.value = goldenClassMeta(rawPrev).code;
  }
}

function populateGoldenClassSelects() {
  fillGoldenClassSelect($('#golden-expected-class'), null);
}

function inferGoldenPolarityKind(classCode) {
  const c = String(classCode || '').toLowerCase();
  if (!c) return 'generic';
  if (c.includes('scap') || c.includes('electrolyt') || (c.includes('cap') && !c.includes('ceramic'))) {
    return 'electrolytic';
  }
  if (c.includes('diode')) return 'diode';
  if (c.includes('ic') || c.includes('chip') || c.includes('micro')) return 'ic';
  return 'generic';
}

function renderGoldenRegionsList() {
  const wrap = $('#golden-regions-list');
  if (!wrap) return;
  const regs = state.goldenBoard.regions || [];
  wrap.innerHTML = '';
  if (!regs.length) {
    wrap.innerHTML = '<p class="hint empty-hint">Нет зон — нарисуйте рамку на снимке</p>';
    return;
  }

  const table = document.createElement('table');
  table.className = 'table golden-regions-table';
  const thead = document.createElement('thead');
  thead.innerHTML = '<tr><th>#</th><th>Класс</th><th>Полярность</th><th>Тип</th><th></th></tr>';
  table.appendChild(thead);
  const tbody = document.createElement('tbody');

  regs.forEach((r, idx) => {
    const tr = document.createElement('tr');
    if (idx === state.goldenBoard.selectedRegionIdx) tr.classList.add('selected');

    const tdN = document.createElement('td');
    tdN.textContent = String(idx + 1);
    tdN.title = `${r.x1},${r.y1} — ${r.x2},${r.y2}`;

    const tdCls = document.createElement('td');
    const sel = document.createElement('select');
    sel.title = 'Ожидаемый класс YOLO в этой зоне';
    fillGoldenClassSelect(sel, r.label || '');
    sel.addEventListener('mousedown', (ev) => ev.stopPropagation());
    sel.addEventListener('click', (ev) => ev.stopPropagation());
    sel.addEventListener('change', () => {
      r.label = sel.value || null;
      if (r.check_polarity && (!r.polarity_kind || r.polarity_kind === 'generic')) {
        r.polarity_kind = inferGoldenPolarityKind(r.label);
        kindSel.value = r.polarity_kind;
      }
      redrawGoldenRegions();
    });
    tdCls.appendChild(sel);

    const tdPol = document.createElement('td');
    const polWrap = document.createElement('label');
    polWrap.style.display = 'inline-flex';
    polWrap.style.alignItems = 'center';
    polWrap.style.gap = '6px';
    polWrap.style.fontSize = '0.85rem';
    const polCb = document.createElement('input');
    polCb.type = 'checkbox';
    polCb.checked = !!r.check_polarity;
    polCb.title = 'Проверять полярность/ориентацию маркера при инспекции';
    polCb.addEventListener('click', (ev) => ev.stopPropagation());
    polCb.addEventListener('change', () => {
      r.check_polarity = polCb.checked;
      if (r.check_polarity && !r.polarity_marker) {
        state.goldenBoard.drawMode = 'marker';
        state.goldenBoard.selectedRegionIdx = idx;
        syncGoldenDrawModeUi();
      }
      redrawGoldenRegions();
    });
    polWrap.appendChild(polCb);
    polWrap.appendChild(document.createTextNode('Проверять'));
    tdPol.appendChild(polWrap);
    const markBtn = document.createElement('button');
    markBtn.type = 'button';
    markBtn.className = 'btn btn--ghost btn--sm';
    markBtn.textContent = r.polarity_marker ? 'Маркер ✓' : 'Маркер';
    markBtn.title = 'Нарисовать синюю рамку маркера полярности (полоска катода, pin1…)';
    markBtn.style.marginLeft = '4px';
    markBtn.style.color = '#111';
    markBtn.addEventListener('click', (ev) => {
      ev.stopPropagation();
      state.goldenBoard.selectedRegionIdx = idx;
      state.goldenBoard.drawMode = 'marker';
      syncGoldenDrawModeUi();
      renderGoldenRegionsList();
    });
    tdPol.appendChild(markBtn);

    const tdKind = document.createElement('td');
    const kindSel = document.createElement('select');
    kindSel.title = 'Тип компонента для проверки полярности';
    [
      ['electrolytic', 'Электролит.'],
      ['diode', 'Диод'],
      ['ic', 'Микросхема'],
      ['generic', 'Общий'],
    ].forEach(([val, label]) => {
      const opt = document.createElement('option');
      opt.value = val;
      opt.textContent = label;
      kindSel.appendChild(opt);
    });
    kindSel.value = r.polarity_kind || 'generic';
    kindSel.addEventListener('mousedown', (ev) => ev.stopPropagation());
    kindSel.addEventListener('change', () => {
      r.polarity_kind = kindSel.value;
    });
    tdKind.appendChild(kindSel);

    const tdDel = document.createElement('td');
    const del = document.createElement('button');
    del.type = 'button';
    del.className = 'btn btn--ghost btn-delete-region';
    del.textContent = '×';
    del.title = 'Удалить зону';
    del.addEventListener('click', (ev) => {
      ev.stopPropagation();
      state.goldenBoard.regions.splice(idx, 1);
      if (state.goldenBoard.selectedRegionIdx === idx) {
        state.goldenBoard.selectedRegionIdx = null;
      } else if (state.goldenBoard.selectedRegionIdx != null && state.goldenBoard.selectedRegionIdx > idx) {
        state.goldenBoard.selectedRegionIdx -= 1;
      }
      redrawGoldenRegions();
      renderGoldenRegionsList();
    });
    tdDel.appendChild(del);

    tr.addEventListener('click', (ev) => {
      if (ev.target.closest('select, button, option')) return;
      state.goldenBoard.selectedRegionIdx = idx;
      renderGoldenRegionsList();
      redrawGoldenRegions();
    });

    tr.appendChild(tdN);
    tr.appendChild(tdCls);
    tr.appendChild(tdPol);
    tr.appendChild(tdKind);
    tr.appendChild(tdDel);
    tbody.appendChild(tr);
  });

  table.appendChild(tbody);
  wrap.appendChild(table);
}

function syncGoldenDrawModeUi() {
  const btn = $('#btn-golden-draw-marker');
  const isMarker = state.goldenBoard.drawMode === 'marker';
  if (btn) {
    btn.classList.toggle('is-active', isMarker);
    btn.setAttribute('aria-pressed', isMarker ? 'true' : 'false');
  }
  const hint = $('#golden-editor-hint');
  if (hint && state.goldenBoard.selectedId) {
    const extra = isMarker
      ? ' Режим маркера полярности: выделите полоску катода / pin1 и соседний участок корпуса (не только однотонную полосу).'
      : '';
    if (!hint.dataset.baseHint) {
      hint.dataset.baseHint = hint.textContent || '';
    }
    if (!isMarker) {
      hint.textContent = hint.dataset.baseHint;
    } else {
      hint.textContent = (hint.dataset.baseHint || '') + extra;
    }
  }
}

function goldenRegionsForSave() {
  return (state.goldenBoard.regions || []).map((r) => {
    const out = {
      x1: r.x1,
      y1: r.y1,
      x2: r.x2,
      y2: r.y2,
      label: r.label || null,
      check_polarity: !!r.check_polarity,
      polarity_kind: r.polarity_kind || 'generic',
    };
    if (r.polarity_marker) {
      out.polarity_marker = { ...r.polarity_marker };
    }
    return out;
  });
}

function goldenHitTestRegion(nx, ny) {
  const regs = state.goldenBoard.regions || [];
  for (let i = regs.length - 1; i >= 0; i -= 1) {
    const r = regs[i];
    if (nx >= r.x1 && nx <= r.x2 && ny >= r.y1 && ny <= r.y2) return i;
  }
  return null;
}

function normalizeGoldenRegion(r) {
  if (!r || typeof r !== 'object') return null;
  let x1; let y1; let x2; let y2;
  if (Number.isFinite(r.x1) && Number.isFinite(r.x2)) {
    x1 = r.x1; y1 = r.y1; x2 = r.x2; y2 = r.y2;
  } else if (Number.isFinite(r.x) && Number.isFinite(r.w) && Number.isFinite(r.h)) {
    x1 = r.x; y1 = r.y; x2 = r.x + r.w; y2 = r.y + r.h;
  } else {
    return null;
  }
  const kinds = ['electrolytic', 'diode', 'ic', 'generic'];
  let kind = kinds.includes(r.polarity_kind) ? r.polarity_kind : 'generic';
  if (kind === 'generic' && r.label) {
    kind = inferGoldenPolarityKind(r.label);
  }
  let polarity_marker = null;
  const pm = r.polarity_marker;
  if (pm && Number.isFinite(pm.x1) && Number.isFinite(pm.x2) && pm.x2 > pm.x1 && pm.y2 > pm.y1) {
    polarity_marker = { x1: pm.x1, y1: pm.y1, x2: pm.x2, y2: pm.y2 };
  }
  return {
    x1, y1, x2, y2,
    label: r.label || null,
    check_polarity: !!r.check_polarity,
    polarity_kind: kind,
    polarity_marker,
  };
}

function redrawGoldenRegions() {
  const cv = $('#golden-ref-canvas');
  if (!cv || !cv.getContext) return;
  const ctx = cv.getContext('2d');
  if (!ctx) return;
  ctx.clearRect(0, 0, cv.width, cv.height);
  const regs = state.goldenBoard.regions || [];
  ctx.lineWidth = 2;
  regs.forEach((r, idx) => {
    const selected = idx === state.goldenBoard.selectedRegionIdx;
    ctx.strokeStyle = goldenClassStrokeColor(r.label, selected);
    ctx.lineWidth = selected ? 3 : 2;
    ctx.strokeRect(r.x1, r.y1, r.x2 - r.x1, r.y2 - r.y1);
    const caption = goldenClassDisplayName(r.label);
    ctx.fillStyle = selected ? 'rgba(234, 179, 8, 0.92)' : 'rgba(34, 197, 94, 0.88)';
    ctx.font = 'bold 14px sans-serif';
    ctx.fillText(`#${idx + 1} ${caption}`, r.x1 + 4, Math.max(16, r.y1 + 16));
    if (r.check_polarity && r.polarity_marker) {
      const pm = r.polarity_marker;
      ctx.save();
      ctx.strokeStyle = '#2563eb';
      ctx.lineWidth = selected ? 3 : 2;
      ctx.setLineDash([6, 4]);
      ctx.strokeRect(pm.x1, pm.y1, pm.x2 - pm.x1, pm.y2 - pm.y1);
      ctx.setLineDash([]);
      ctx.fillStyle = 'rgba(37, 99, 235, 0.85)';
      ctx.font = 'bold 12px sans-serif';
      ctx.fillText('±', pm.x1 + 4, Math.max(pm.y1 + 14, 14));
      ctx.restore();
    }
  });
  ctx.lineWidth = 2;
  const d = state.goldenBoard.drag;
  if (d && d.x1 != null && d.x2 != null) {
    ctx.strokeStyle = '#eab308';
    const x1 = Math.min(d.x1, d.x2);
    const y1 = Math.min(d.y1, d.y2);
    const x2 = Math.max(d.x1, d.x2);
    const y2 = Math.max(d.y1, d.y2);
    ctx.strokeRect(x1, y1, x2 - x1, y2 - y1);
  }
  const md = state.goldenBoard.markerDrag;
  if (md && md.x1 != null && md.x2 != null) {
    ctx.strokeStyle = '#2563eb';
    ctx.setLineDash([6, 4]);
    const x1 = Math.min(md.x1, md.x2);
    const y1 = Math.min(md.y1, md.y2);
    const x2 = Math.max(md.x1, md.x2);
    const y2 = Math.max(md.y1, md.y2);
    ctx.strokeRect(x1, y1, x2 - x1, y2 - y1);
    ctx.setLineDash([]);
  }
}

function scheduleFitGoldenCanvas() {
  requestAnimationFrame(() => {
    fitGoldenCanvasToImage();
    requestAnimationFrame(() => fitGoldenCanvasToImage());
  });
}

function fitGoldenCanvasToImage() {
  const img = $('#golden-ref-img');
  const cv = $('#golden-ref-canvas');
  if (!img || !cv || !img.naturalWidth) return;
  const nw = img.naturalWidth;
  const nh = img.naturalHeight;
  cv.width = nw;
  cv.height = nh;
  const displayW = img.clientWidth || nw;
  const displayH = img.clientHeight || Math.round(nh * (displayW / nw));
  cv.style.width = `${displayW}px`;
  cv.style.height = `${displayH}px`;
  redrawGoldenRegions();
}

function buildGoldenDesignateSelect(row) {
  const sel = document.createElement('select');
  sel.className = 'device-designate-select';
  sel.title = 'Закрепить эталон за сотрудником';
  const empty = document.createElement('option');
  empty.value = '';
  empty.textContent = '— не закреплено —';
  sel.appendChild(empty);
  (state.deviceOperatorOptions || []).forEach((u) => {
    const opt = document.createElement('option');
    opt.value = String(u.id);
    opt.textContent = `${u.username}${u.full_name ? ` — ${u.full_name}` : ''}`;
    sel.appendChild(opt);
  });
  sel.value = row.designated_operator_id ? String(row.designated_operator_id) : '';
  sel.addEventListener('change', async () => {
    const msg = $('#golden-list-msg');
    if (msg) { msg.textContent = ''; msg.className = 'status'; }
    try {
      await api(`/api/golden-boards/${row.id}`, {
        method: 'PATCH',
        body: { designated_operator_id: sel.value ? Number(sel.value) : null },
      });
      if (msg) {
        msg.classList.add('success');
        msg.textContent = sel.value ? 'Эталон закреплён за сотрудником' : 'Закрепление снято';
      }
      await loadGoldenBoardsList();
    } catch (e) {
      if (msg) {
        msg.classList.add('error');
        msg.textContent = e.message;
      }
      sel.value = row.designated_operator_id ? String(row.designated_operator_id) : '';
    }
  });
  return sel;
}

async function loadGoldenBoardsList() {
  const tb = $('#golden-boards-body');
  const msg = $('#golden-list-msg');
  if (!tb) return;
  if (msg) { msg.textContent = ''; msg.className = 'status'; }
  tb.innerHTML = '<tr><td colspan="6" class="empty">Загрузка…</td></tr>';
  try {
    await ensureDeviceOperatorOptions();
    const rows = await api('/api/golden-boards');
    if (!rows.length) {
      tb.innerHTML = '<tr><td colspan="6" class="empty">Нет эталонов — создайте профиль выше</td></tr>';
      return;
    }
    tb.innerHTML = '';
    rows.forEach((r) => {
      const tr = document.createElement('tr');
      tr.dataset.id = String(r.id);
      const t1 = document.createElement('td');
      t1.textContent = String(r.id);
      const t2 = document.createElement('td');
      t2.textContent = r.name || '';
      const t3 = document.createElement('td');
      t3.textContent = r.board_model || '—';
      const t4 = document.createElement('td');
      t4.appendChild(buildGoldenDesignateSelect(r));
      const t5 = document.createElement('td');
      t5.textContent = r.has_reference_image ? 'да' : 'нет';
      const t6 = document.createElement('td');
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'btn btn--secondary btn-select-golden';
      btn.textContent = 'Открыть';
      t6.appendChild(btn);
      tr.appendChild(t1);
      tr.appendChild(t2);
      tr.appendChild(t3);
      tr.appendChild(t4);
      tr.appendChild(t5);
      tr.appendChild(t6);
      tb.appendChild(tr);
    });
  } catch (e) {
    tb.innerHTML = '<tr><td colspan="6" class="empty">Ошибка загрузки</td></tr>';
    if (msg) {
      msg.classList.add('error');
      msg.textContent = e.message;
    }
  }
}

async function loadGoldenBoardsTab() {
  if (!isElevatedRole()) return;
  await ensureDefectClassesLoaded(true);
  await loadGoldenBoardsList();
}

async function openGoldenEditor(id) {
  const panel = $('#golden-board-editor');
  const title = $('#golden-editor-title');
  const hint = $('#golden-editor-hint');
  const msg = $('#golden-editor-msg');
  if (msg) { msg.textContent = ''; msg.className = 'status'; }
  await ensureDefectClassesLoaded(true);
  state.goldenBoard.selectedId = id;
  state.goldenBoard.drag = null;
  state.goldenBoard.markerDrag = null;
  state.goldenBoard.selectedRegionIdx = null;
  state.goldenBoard.drawMode = 'region';
  if (panel) panel.hidden = false;
  const data = await api(`/api/golden-boards/${id}`);
  const rawRegs = (data.payload && Array.isArray(data.payload.regions)) ? data.payload.regions : [];
  state.goldenBoard.regions = rawRegs.map(normalizeGoldenRegion).filter(Boolean);
  renderGoldenRegionsList();
  if (title) title.textContent = `Эталон №${id}: ${data.name}`;
  const tolIn = $('#golden-region-tolerance-px');
  if (tolIn) {
    const p = data.payload && typeof data.payload === 'object' ? data.payload : {};
    const tol = p.region_tolerance_px;
    tolIn.value = String(
      tol != null && Number.isFinite(Number(tol)) ? Math.max(0, Math.min(128, Number(tol))) : 12
    );
  }
  if (hint) {
    const clsHint = (state.defectClasses?.length)
      ? ` Доступно классов модели: ${state.defectClasses.length}.`
      : '';
    hint.textContent = (data.reference_image_url
      ? 'Тяните мышью рамку на снимке (координаты в пикселях файла эталона).'
      : 'Сначала загрузите опорный снимок (JPEG/PNG, не меньше 640×640).') + clsHint;
    hint.dataset.baseHint = hint.textContent;
  }
  syncGoldenDrawModeUi();
  const img = $('#golden-ref-img');
  revokeGoldenBoardImageUrl();
  if (data.reference_image_url && img) {
    await loadImageWithAuth(data.reference_image_url, img);
    if (img.complete) scheduleFitGoldenCanvas();
    else {
      img.onload = () => { scheduleFitGoldenCanvas(); };
    }
  } else {
    const cv = $('#golden-ref-canvas');
    if (cv) {
      const ctx = cv.getContext('2d');
      if (ctx) ctx.clearRect(0, 0, cv.width, cv.height);
    }
  }
}

$('#golden-new-form')?.addEventListener('submit', async (ev) => {
  ev.preventDefault();
  const msg = $('#golden-list-msg');
  if (msg) { msg.textContent = ''; msg.className = 'status'; }
  const fd = new FormData(ev.target);
  const name = (fd.get('name') || '').trim();
  const bm = (fd.get('board_model') || '').trim();
  if (!name) return;
  try {
    await api('/api/golden-boards', {
      method: 'POST',
      body: {
        name,
        board_model: bm || null,
        payload: { regions: [], region_tolerance_px: 12 },
      },
    });
    if (msg) {
      msg.classList.add('success');
      msg.textContent = 'Профиль создан';
    }
    ev.target.reset();
    await loadGoldenBoardsList();
    await loadGoldenProfileChoices();
  } catch (e) {
    if (msg) {
      msg.classList.add('error');
      msg.textContent = e.message;
    }
  }
});

$('#golden-boards-body')?.addEventListener('click', async (ev) => {
  const btn = ev.target.closest('.btn-select-golden');
  if (!btn) return;
  const tr = btn.closest('tr');
  const id = tr?.dataset?.id;
  if (!id) return;
  try {
    await openGoldenEditor(Number(id));
  } catch (e) {
    const msg = $('#golden-list-msg');
    if (msg) {
      msg.classList.add('error');
      msg.textContent = e.message;
    }
  }
});

$('#golden-ref-file')?.addEventListener('change', async (ev) => {
  const id = state.goldenBoard.selectedId;
  const f = ev.target.files?.[0];
  ev.target.value = '';
  const msg = $('#golden-editor-msg');
  if (!id || !f) return;
  if (msg) { msg.textContent = ''; msg.className = 'status'; }
  try {
    const fd = new FormData();
    fd.append('image', f, f.name);
    const autoCb = $('#golden-auto-markup-upload');
    fd.append('auto_markup', autoCb && !autoCb.checked ? 'false' : 'true');
    await api(`/api/golden-boards/${id}/reference-image`, { method: 'POST', body: fd });
    if (msg) {
      msg.classList.add('success');
      msg.textContent = 'Снимок сохранён';
    }
    await openGoldenEditor(id);
    await loadGoldenBoardsList();
  } catch (e) {
    if (msg) {
      msg.classList.add('error');
      msg.textContent = e.message;
    }
  }
});

$('#btn-golden-auto-markup')?.addEventListener('click', async () => {
  const id = state.goldenBoard.selectedId;
  const msg = $('#golden-editor-msg');
  if (!id) return;
  if (!window.confirm('Заменить текущую разметку результатами YOLO на опорном снимке?')) return;
  if (msg) { msg.textContent = ''; msg.className = 'status'; }
  try {
    const data = await api(`/api/golden-boards/${id}/auto-markup?replace=true`, { method: 'POST' });
    const rawRegs = (data.payload && Array.isArray(data.payload.regions)) ? data.payload.regions : [];
    state.goldenBoard.regions = rawRegs.map(normalizeGoldenRegion).filter(Boolean);
    state.goldenBoard.selectedRegionIdx = null;
    renderGoldenRegionsList();
    redrawGoldenRegions();
    scheduleFitGoldenCanvas();
    if (msg) {
      msg.classList.add('success');
      msg.textContent = `Авторазметка: ${state.goldenBoard.regions.length} зон (сохраните при необходимости)`;
    }
  } catch (e) {
    if (msg) {
      msg.classList.add('error');
      msg.textContent = e.message;
    }
  }
});

$('#btn-golden-save-markup')?.addEventListener('click', async () => {
  const id = state.goldenBoard.selectedId;
  const msg = $('#golden-editor-msg');
  if (!id) return;
  if (msg) { msg.textContent = ''; msg.className = 'status'; }
  try {
    const tolRaw = $('#golden-region-tolerance-px')?.value?.trim();
    const tol = tolRaw !== '' && tolRaw != null && Number.isFinite(Number(tolRaw))
      ? Math.max(0, Math.min(128, parseInt(tolRaw, 10)))
      : 12;
    await api(`/api/golden-boards/${id}/markup`, {
      method: 'PUT',
      body: { regions: goldenRegionsForSave(), region_tolerance_px: tol },
    });
    if (msg) {
      msg.classList.add('success');
      msg.textContent = 'Разметка сохранена';
    }
  } catch (e) {
    if (msg) {
      msg.classList.add('error');
      msg.textContent = e.message;
    }
  }
});

$('#btn-golden-clear-local')?.addEventListener('click', () => {
  state.goldenBoard.regions = [];
  state.goldenBoard.drag = null;
  state.goldenBoard.selectedRegionIdx = null;
  redrawGoldenRegions();
  renderGoldenRegionsList();
});

$('#btn-golden-delete-profile')?.addEventListener('click', async () => {
  const id = state.goldenBoard.selectedId;
  if (!id) return;
  if (!window.confirm(`Удалить эталон №${id}? Это необратимо.`)) return;
  const msg = $('#golden-editor-msg');
  if (msg) { msg.textContent = ''; msg.className = 'status'; }
  try {
    await api(`/api/golden-boards/${id}`, { method: 'DELETE' });
    const panel = $('#golden-board-editor');
    if (panel) panel.hidden = true;
    state.goldenBoard.selectedId = null;
    state.goldenBoard.regions = [];
    revokeGoldenBoardImageUrl();
    await loadGoldenBoardsList();
    await loadGoldenProfileChoices();
  } catch (e) {
    if (msg) {
      msg.classList.add('error');
      msg.textContent = e.message;
    }
  }
});

$('#btn-golden-draw-marker')?.addEventListener('click', () => {
  if (!state.goldenBoard.selectedId) return;
  if (state.goldenBoard.selectedRegionIdx == null && state.goldenBoard.regions?.length) {
    state.goldenBoard.selectedRegionIdx = 0;
  }
  state.goldenBoard.drawMode = state.goldenBoard.drawMode === 'marker' ? 'region' : 'marker';
  syncGoldenDrawModeUi();
  renderGoldenRegionsList();
});

$('#golden-ref-canvas')?.addEventListener('mousedown', (ev) => {
  if (!state.goldenBoard.selectedId) return;
  const cv = ev.target;
  if (!(cv instanceof HTMLCanvasElement)) return;
  const [nx, ny] = goldenOffsetToNatural(cv, ev.offsetX, ev.offsetY);

  if (state.goldenBoard.drawMode === 'marker') {
    const idx = state.goldenBoard.selectedRegionIdx;
    if (idx == null || !state.goldenBoard.regions[idx]) {
      return;
    }
    state.goldenBoard.markerDrag = { x1: nx, y1: ny, x2: nx, y2: ny };
    redrawGoldenRegions();
    return;
  }

  const hit = goldenHitTestRegion(nx, ny);
  if (hit != null && !ev.shiftKey) {
    state.goldenBoard.selectedRegionIdx = hit;
    state.goldenBoard.drag = null;
    renderGoldenRegionsList();
    redrawGoldenRegions();
    return;
  }
  state.goldenBoard.drag = { x1: nx, y1: ny, x2: nx, y2: ny };
  redrawGoldenRegions();
});

$('#golden-ref-canvas')?.addEventListener('mousemove', (ev) => {
  const d = state.goldenBoard.drag;
  const md = state.goldenBoard.markerDrag;
  const cv = ev.target;
  if (!(cv instanceof HTMLCanvasElement)) return;
  const [nx, ny] = goldenOffsetToNatural(cv, ev.offsetX, ev.offsetY);
  if (md && md.x1 != null) {
    md.x2 = nx;
    md.y2 = ny;
    redrawGoldenRegions();
    return;
  }
  if (!d || d.x1 == null) return;
  d.x2 = nx;
  d.y2 = ny;
  redrawGoldenRegions();
});

$('#golden-ref-canvas')?.addEventListener('mouseup', (ev) => {
  const cv = ev.target;
  if (!(cv instanceof HTMLCanvasElement)) return;
  const [nx, ny] = goldenOffsetToNatural(cv, ev.offsetX, ev.offsetY);

  const md = state.goldenBoard.markerDrag;
  if (md && md.x1 != null) {
    md.x2 = nx;
    md.y2 = ny;
    const x1 = Math.min(md.x1, md.x2);
    const y1 = Math.min(md.y1, md.y2);
    const x2 = Math.max(md.x1, md.x2);
    const y2 = Math.max(md.y1, md.y2);
    state.goldenBoard.markerDrag = null;
    const idx = state.goldenBoard.selectedRegionIdx;
    if (idx != null && x2 - x1 > 3 && y2 - y1 > 3 && state.goldenBoard.regions[idx]) {
      const r = state.goldenBoard.regions[idx];
      r.polarity_marker = { x1, y1, x2, y2 };
      r.check_polarity = true;
      renderGoldenRegionsList();
    }
    redrawGoldenRegions();
    return;
  }

  const d = state.goldenBoard.drag;
  if (!d || d.x1 == null) return;
  d.x2 = nx;
  d.y2 = ny;
  const x1 = Math.min(d.x1, d.x2);
  const y1 = Math.min(d.y1, d.y2);
  const x2 = Math.max(d.x1, d.x2);
  const y2 = Math.max(d.y1, d.y2);
  state.goldenBoard.drag = null;
  if (x2 - x1 > 4 && y2 - y1 > 4) {
    const sel = $('#golden-expected-class');
    const label = sel && sel.value ? String(sel.value) : null;
    state.goldenBoard.regions.push({
      x1, y1, x2, y2, label,
      check_polarity: false,
      polarity_kind: 'generic',
      polarity_marker: null,
    });
    state.goldenBoard.selectedRegionIdx = state.goldenBoard.regions.length - 1;
    renderGoldenRegionsList();
  }
  redrawGoldenRegions();
});

$('#golden-ref-canvas')?.addEventListener('mouseleave', () => {
  if (state.goldenBoard.drag) state.goldenBoard.drag = null;
  if (state.goldenBoard.markerDrag) state.goldenBoard.markerDrag = null;
  redrawGoldenRegions();
});

$('#golden-ref-img')?.addEventListener('load', () => {
  scheduleFitGoldenCanvas();
});

window.addEventListener('resize', () => {
  const gi = $('#golden-ref-img');
  if (state.goldenBoard.selectedId && gi && gi.naturalWidth) {
    scheduleFitGoldenCanvas();
  }
});

// ---------- Инициализация ----------
async function bootApp() {
  try {
    const meta = await fetch('/api/meta', state.token ? { headers: { Authorization: `Bearer ${state.token}` } } : undefined)
      .then((r) => r.ok ? r.json() : null);
    if (meta) {
      state.defectClasses = meta.defect_classes || [];
      state.classSemantics = meta.class_semantics || {};
      state.meta = { ...state.meta, ...meta };
      const sel = $('#history-filter select[name="class_code"]');
      if (sel && !sel.dataset.populated) {
        state.defectClasses.forEach((c) => {
          const opt = document.createElement('option');
          opt.value = c.code; opt.textContent = c.name;
          sel.appendChild(opt);
        });
        sel.dataset.populated = '1';
      }
      const gsel = $('#golden-expected-class');
      if (gsel && !gsel.dataset.populated) {
        populateGoldenClassSelects();
        gsel.dataset.populated = '1';
      }
    }
  } catch {}

  if (!state.token) { showScreen('login'); return; }
  try {
    const me = await api('/api/auth/me');
    state.role = me.role;
    state.user = me.username;
    state.currentUserId = me.id;
    localStorage.setItem(ROLE_KEY, me.role);
    localStorage.setItem(USER_KEY, me.username);
    $('#user-label').textContent = `${me.full_name || me.username} · ${roleRu(me.role)}`;
    applyRoleVisibility();
    showScreen('app');
    if (!isElevatedRole() && isInspectPanelHiddenPersisted()) {
      closeReview();
      teardownInspectResultDomOnly();
    }
    updateHistoryUnhideButton();
    startRegistryEventSource();
    await refreshMyDevice();
    loadDevices();
    await loadGoldenProfileChoices();
  } catch {
    logout();
  }
}

document.addEventListener('visibilitychange', () => {
  if (document.hidden) { stopLive(); }
});

bootApp();
