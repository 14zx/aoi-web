/* АОИ-Web · страница телефона-камеры (fullscreen IP-camera).
 *
 * URL-параметры:
 *   /phone?device=<id>&token=<upload_token>
 *
 * Устройство и ссылка создаются в разделе «Устройства» на компьютере (руководитель
 * или администратор). После открытия ссылки пользователь разрешает доступ к камере и нажимает кнопку
 * «старт» — телефон начинает непрерывно публиковать JPEG-кадры на
 * POST /api/devices/{id}/frame с заголовком X-Device-Token.
 */
(function () {
  "use strict";

  const qs = new URLSearchParams(location.search);
  const DEVICE_ID = Number(qs.get("device") || 0) || null;
  const UPLOAD_TOKEN = qs.get("token") || "";

  // -------------------- Пресеты качества --------------------
  const PRESETS = {
    sd:  { label: "SD",      width: 854,  height: 480,  fps: 5,  quality: 0.70, fpsRequest: 24 },
    hd:  { label: "HD",      width: 1280, height: 720,  fps: 8,  quality: 0.82, fpsRequest: 30 },
    fhd: { label: "FullHD",  width: 1920, height: 1080, fps: 10, quality: 0.88, fpsRequest: 30 },
    max: { label: "MAX",     width: 0,    height: 0,    fps: 12, quality: 0.92, fpsRequest: 60 },
  };
  const PRESET_ORDER = ["sd", "hd", "fhd", "max"];
  const STORAGE_KEY = "aoi-phone-preset-v1";
  const FACING_KEY  = "aoi-phone-facing-v1";

  // -------------------- Состояние --------------------
  const state = {
    presetKey: localStorage.getItem(STORAGE_KEY) || "hd",
    facingMode: localStorage.getItem(FACING_KEY) || "environment",
    stream: null,
    track: null,
    running: false,
    loopTimer: null,
    frameCounter: 0,
    fpsCounter: 0,
    fpsTimer: null,
    lastFpsTs: 0,
    canvas: document.createElement("canvas"),
    torchOn: false,
    // Опрос удалённых команд + публикация статуса.
    commandsTimer: null,
    statusTimer: null,
  };

  // -------------------- DOM --------------------
  const $ = (id) => document.getElementById(id);
  const dom = {
    video:         $("preview"),
    stage:         $("stage"),
    deviceTitle:   $("deviceTitle"),
    statusDot:     $("statusDot"),
    statusText:    $("statusText"),
    qualityBtn:    $("qualityBtn"),
    qualityLabel:  $("qualityLabel"),
    flipBtn:       $("flipBtn"),
    torchBtn:      $("torchBtn"),
    fullscreenBtn: $("fullscreenBtn"),
    recordBtn:     $("recordBtn"),
    recordDot:     $("recordDot"),
    fpsLabel:      $("fpsLabel"),
    sizeLabel:     $("sizeLabel"),
    sentLabel:     $("sentLabel"),
    qualitySheet:  $("qualitySheet"),
    qualityClose:  $("qualityClose"),
    qualityOptions: $("qualityOptions"),
    overlay:       $("overlay"),
    overlayTitle:  $("overlayTitle"),
    overlayText:   $("overlayText"),
    overlayAction: $("overlayAction"),
    infoBtn:       $("infoBtn"),
  };

  // -------------------- Утилиты --------------------
  function showOverlay(title, text, actionLabel) {
    dom.overlay.classList.remove("hidden");
    dom.overlayTitle.textContent = title;
    dom.overlayText.textContent = text || "";
    if (actionLabel) {
      dom.overlayAction.hidden = false;
      dom.overlayAction.textContent = actionLabel;
    } else {
      dom.overlayAction.hidden = true;
    }
  }

  function hideOverlay() {
    dom.overlay.classList.add("hidden");
  }

  function setStatus(kind, text) {
    dom.statusDot.classList.remove("live", "ok", "warn", "err");
    if (kind) dom.statusDot.classList.add(kind);
    dom.statusText.textContent = text;
  }

  function setPresetLabel() {
    dom.qualityLabel.textContent = PRESETS[state.presetKey].label;
  }

  function buildConstraints() {
    const p = PRESETS[state.presetKey];
    const video = {
      facingMode: { ideal: state.facingMode },
      frameRate: { ideal: p.fpsRequest, max: p.fpsRequest },
    };
    if (p.width && p.height) {
      video.width  = { ideal: p.width };
      video.height = { ideal: p.height };
    } else {
      // MAX — просим максимум возможного
      video.width  = { ideal: 3840 };
      video.height = { ideal: 2160 };
    }
    return { audio: false, video };
  }

  // -------------------- Поток камеры --------------------
  async function openCamera() {
    try {
      closeCamera();
      setStatus("warn", "Запрос доступа к камере…");
      const stream = await navigator.mediaDevices.getUserMedia(buildConstraints());
      state.stream = stream;
      state.track = stream.getVideoTracks()[0] || null;
      dom.video.srcObject = stream;
      await dom.video.play().catch(() => {});

      const s = state.track ? state.track.getSettings() : {};
      if (s && s.width && s.height) {
        dom.sizeLabel.textContent = `${s.width}×${s.height}`;
      }
      // Проверка наличия torch
      if (state.track && typeof state.track.getCapabilities === "function") {
        const caps = state.track.getCapabilities() || {};
        dom.torchBtn.hidden = !caps.torch;
      } else {
        dom.torchBtn.hidden = true;
      }
      setStatus("ok", "Камера готова");
      return true;
    } catch (err) {
      console.error(err);
      showOverlay(
        "Нет доступа к камере",
        String((err && err.message) || err) + ". Разрешите доступ в настройках браузера и перезагрузите страницу.",
      );
      setStatus("err", "Ошибка камеры");
      return false;
    }
  }

  function closeCamera() {
    if (state.stream) {
      state.stream.getTracks().forEach((t) => t.stop());
    }
    state.stream = null;
    state.track = null;
    dom.video.srcObject = null;
  }

  async function toggleFacing() {
    state.facingMode = state.facingMode === "environment" ? "user" : "environment";
    localStorage.setItem(FACING_KEY, state.facingMode);
    const wasRunning = state.running;
    if (wasRunning) await stopStreaming();
    await openCamera();
    if (wasRunning) await startStreaming();
  }

  async function toggleTorch() {
    await setTorch(!state.torchOn);
    publishStatus();
  }

  // -------------------- Отправка кадров --------------------
  async function grabAndUpload() {
    if (!state.running) return;
    const track = state.track;
    const video = dom.video;
    if (!track || !video.videoWidth) return;

    const preset = PRESETS[state.presetKey];
    // Для MAX — реальные размеры потока; иначе downscale к запрошенному.
    let outW = preset.width || video.videoWidth;
    let outH = preset.height || video.videoHeight;
    const vw = video.videoWidth;
    const vh = video.videoHeight;
    if (!preset.width) {
      outW = vw;
      outH = vh;
    } else if (vw < outW) {
      // Если камера отдала меньше — отправляем как есть
      outW = vw;
      outH = vh;
    }

    const cvs = state.canvas;
    if (cvs.width !== outW) cvs.width = outW;
    if (cvs.height !== outH) cvs.height = outH;
    const ctx = cvs.getContext("2d", { alpha: false });
    ctx.drawImage(video, 0, 0, outW, outH);

    const blob = await new Promise((res) =>
      cvs.toBlob(res, "image/jpeg", preset.quality)
    );
    if (!blob) return;

    const fd = new FormData();
    fd.append("image", blob, "frame.jpg");

    try {
      const resp = await fetch(`/api/devices/${DEVICE_ID}/frame`, {
        method: "POST",
        headers: { "X-Device-Token": UPLOAD_TOKEN },
        body: fd,
        cache: "no-store",
      });
      if (resp.status === 204) {
        state.frameCounter += 1;
        state.fpsCounter += 1;
        dom.sentLabel.textContent = `${state.frameCounter} кадров`;
        dom.sizeLabel.textContent = `${outW}×${outH} · ${(blob.size/1024).toFixed(0)} КБ`;
      } else {
        const txt = await resp.text().catch(() => "");
        setStatus("err", `HTTP ${resp.status}`);
        if (resp.status === 401) {
          await stopStreaming();
          showOverlay(
            "Устройство недействительно",
            "Ссылка устарела или отменена. Попросите новую в разделе «Устройства» на компьютере.",
          );
        } else {
          console.warn("Upload failed:", resp.status, txt);
        }
      }
    } catch (err) {
      setStatus("err", "Нет сети");
      console.warn(err);
    }
  }

  function scheduleLoop() {
    clearInterval(state.loopTimer);
    const p = PRESETS[state.presetKey];
    const interval = Math.max(80, Math.round(1000 / p.fps));
    state.loopTimer = setInterval(grabAndUpload, interval);
  }

  function startFpsCounter() {
    stopFpsCounter();
    state.lastFpsTs = performance.now();
    state.fpsTimer = setInterval(() => {
      const now = performance.now();
      const dt = (now - state.lastFpsTs) / 1000 || 1;
      const fps = state.fpsCounter / dt;
      dom.fpsLabel.textContent = `${fps.toFixed(1)} к/с`;
      state.fpsCounter = 0;
      state.lastFpsTs = now;
    }, 1000);
  }
  function stopFpsCounter() {
    if (state.fpsTimer) clearInterval(state.fpsTimer);
    state.fpsTimer = null;
  }

  async function startStreaming() {
    if (!state.stream) {
      const ok = await openCamera();
      if (!ok) return;
    }
    state.running = true;
    dom.recordBtn.classList.add("recording");
    setStatus("live", "В эфире");
    scheduleLoop();
    startFpsCounter();
    lockScreenAwake();
    publishStatus();
  }

  async function stopStreaming() {
    state.running = false;
    clearInterval(state.loopTimer);
    state.loopTimer = null;
    stopFpsCounter();
    dom.recordBtn.classList.remove("recording");
    setStatus("ok", "Готов");
    releaseScreenAwake();
    publishStatus();
  }

  // -------------------- Удалённое управление (PC → телефон) --------------------
  async function fetchRemoteCommands() {
    if (!DEVICE_ID) return;
    try {
      const r = await fetch(`/api/devices/${DEVICE_ID}/commands`, {
        headers: { "X-Device-Token": UPLOAD_TOKEN },
        cache: "no-store",
      });
      if (!r.ok) return;
      const data = await r.json();
      for (const c of (data.commands || [])) {
        try { await handleRemoteCommand(c); } catch (e) { console.warn("cmd fail", e); }
      }
    } catch (_) { /* временные ошибки сети — тихо */ }
  }

  async function handleRemoteCommand(c) {
    switch (c.command) {
      case "start":
        if (!state.running) await startStreaming();
        break;
      case "stop":
        if (state.running) await stopStreaming();
        break;
      case "torch_on":
        await setTorch(true);
        break;
      case "torch_off":
        await setTorch(false);
        break;
      case "flip":
        await toggleFacing();
        break;
      case "quality":
        if (c.value && PRESETS[c.value]) await pickPreset(c.value);
        break;
      default:
        console.warn("unknown command:", c);
    }
    // Любая команда изменяет публикуемый статус.
    publishStatus();
  }

  async function publishStatus() {
    if (!DEVICE_ID) return;
    try {
      await fetch(`/api/devices/${DEVICE_ID}/status`, {
        method: "POST",
        headers: {
          "X-Device-Token": UPLOAD_TOKEN,
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          is_streaming: !!state.running,
          preset: state.presetKey,
          torch_on: !!state.torchOn,
          facing: state.facingMode,
        }),
        cache: "no-store",
      });
    } catch (_) { /* тихо */ }
  }

  async function setTorch(on) {
    if (!state.track) return;
    try {
      await state.track.applyConstraints({ advanced: [{ torch: !!on }] });
      state.torchOn = !!on;
      dom.torchBtn.classList.toggle("active", state.torchOn);
    } catch (err) {
      console.warn("Torch unsupported:", err);
    }
  }

  // -------------------- Полноэкранный режим --------------------
  async function toggleFullscreen() {
    try {
      if (!document.fullscreenElement) {
        await (dom.stage.requestFullscreen?.() ||
               dom.stage.webkitRequestFullscreen?.());
      } else {
        await (document.exitFullscreen?.() ||
               document.webkitExitFullscreen?.());
      }
    } catch (err) {
      console.warn("fullscreen error", err);
    }
  }

  // -------------------- Wake Lock --------------------
  let wakeLock = null;
  async function lockScreenAwake() {
    if (!("wakeLock" in navigator)) return;
    try {
      wakeLock = await navigator.wakeLock.request("screen");
      wakeLock.addEventListener("release", () => { wakeLock = null; });
    } catch (err) {
      console.warn("wakeLock failed", err);
    }
  }
  function releaseScreenAwake() {
    if (wakeLock) { try { wakeLock.release(); } catch (_) {} wakeLock = null; }
  }

  // -------------------- Sheet выбора качества --------------------
  function openQualitySheet() {
    dom.qualityOptions.querySelectorAll("button").forEach((btn) => {
      btn.classList.toggle("active", btn.dataset.preset === state.presetKey);
    });
    dom.qualitySheet.hidden = false;
  }
  function closeQualitySheet() {
    dom.qualitySheet.hidden = true;
  }
  async function pickPreset(key) {
    if (!PRESETS[key]) { closeQualitySheet(); return; }
    const changed = key !== state.presetKey;
    state.presetKey = key;
    localStorage.setItem(STORAGE_KEY, key);
    setPresetLabel();
    closeQualitySheet();
    // Если камера ещё не открыта (юзер на стартовом экране) — просто
    // запоминаем пресет, реальный запрос getUserMedia пойдёт по кнопке
    // «Разрешить и начать».
    publishStatus();
    if (!changed || !state.stream) return;
    const wasRunning = state.running;
    if (wasRunning) await stopStreaming();
    await openCamera();
    if (wasRunning) await startStreaming();
  }

  // -------------------- Старт --------------------
  async function loadDeviceInfo() {
    if (!DEVICE_ID || !UPLOAD_TOKEN) {
      showOverlay(
        "Ссылка недействительна",
        "Откройте страницу по ссылке, которую выдали при создании камеры (раздел «Устройства» на компьютере).",
      );
      return false;
    }
    try {
      const resp = await fetch(
        `/api/devices/public/${DEVICE_ID}?token=${encodeURIComponent(UPLOAD_TOKEN)}`,
        { cache: "no-store" },
      );
      if (!resp.ok) {
        const txt = await resp.text().catch(() => "");
        showOverlay(
          "Устройство недоступно",
          `HTTP ${resp.status}. ${txt || "Возможно, ссылка устарела. Попросите новую в разделе «Устройства» на компьютере."}`,
        );
        return false;
      }
      const data = await resp.json();
      dom.deviceTitle.textContent = data.name || "Камера";
      document.title = `АОИ · ${data.name || "Камера"}`;
      return true;
    } catch (err) {
      showOverlay("Нет связи с сервером", String(err));
      return false;
    }
  }

  async function init() {
    setPresetLabel();

    // Обработчики
    dom.recordBtn.addEventListener("click", async () => {
      if (state.running) await stopStreaming();
      else await startStreaming();
    });
    dom.flipBtn.addEventListener("click", toggleFacing);
    dom.torchBtn.addEventListener("click", toggleTorch);
    dom.fullscreenBtn.addEventListener("click", toggleFullscreen);
    dom.qualityBtn.addEventListener("click", openQualitySheet);
    dom.qualityClose.addEventListener("click", closeQualitySheet);
    dom.qualityOptions.addEventListener("click", (ev) => {
      const btn = ev.target.closest("button[data-preset]");
      if (btn) pickPreset(btn.dataset.preset);
    });
    dom.overlayAction.addEventListener("click", async () => {
      hideOverlay();
      const ok = await openCamera();
      if (ok) await startStreaming();
    });
    dom.infoBtn.addEventListener("click", () => {
      const p = PRESETS[state.presetKey];
      alert(
        `Устройство: ${dom.deviceTitle.textContent}\n` +
        `Качество: ${p.label} (${p.width||"native"}×${p.height||"native"}, ${p.fps} к/с, ${(p.quality*100).toFixed(0)}%)\n` +
        `Отправлено: ${state.frameCounter} кадров`
      );
    });
    document.addEventListener("visibilitychange", () => {
      if (document.hidden && state.running) {
        // Не останавливаем принудительно — пользователь может свернуть, но
        // браузер сам замедлит таймеры. Просто сообщаем.
        setStatus("warn", "Свернуто — кадры могут замедлиться");
      } else if (state.running) {
        setStatus("live", "В эфире");
      }
    });

    const ready = await loadDeviceInfo();
    if (!ready) return;

    // Запускаем опрос команд от PC и периодическую публикацию статуса.
    // Телефон — «подчинённый»: PC может удалённо стопать/запускать запись,
    // включать подсветку и менять качество.
    state.commandsTimer = setInterval(fetchRemoteCommands, 1500);
    state.statusTimer   = setInterval(publishStatus, 3000);
    publishStatus();
    fetchRemoteCommands();

    showOverlay(
      "Разрешите доступ к камере",
      `Это устройство будет транслировать видео в АОИ-Web. Качество по умолчанию — ${PRESETS[state.presetKey].label}. Нажмите кнопку ниже, чтобы начать.`,
      "Разрешить и начать",
    );
  }

  document.addEventListener("DOMContentLoaded", init);
})();
