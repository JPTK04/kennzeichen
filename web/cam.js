/**
 * KENNZEICHEN RADAR • SMARTPHONE AI LIVE-SCANNER
 * YOLOv8 Object Detection & ALPR mit dynamischen Bounding-Boxes & Konfidenz-Anzeige.
 */

const CamState = {
  stream: null,
  facing: 'environment', // Rückkamera
  active: true,
  isDetecting: false,
  platesData: [],
  platesAliases: {},
  tripCount: 0,
  lastCapturedPlates: {}, // Cooldown Map { 'ESB': timestamp }
  audioCtx: null,
  lastDetectionsTime: 0
};

document.addEventListener('DOMContentLoaded', async () => {
  initAudio();
  initEventListeners();
  await loadMasterData();
  await startCamera();
  startAiDetectionLoop();
});

function initAudio() {
  try {
    const AudioContext = window.AudioContext || window.webkitAudioContext;
    if (AudioContext) {
      CamState.audioCtx = new AudioContext();
    }
  } catch (e) {
    console.warn("AudioContext nicht verfügbar", e);
  }
}

function playVictoryChime() {
  if (!CamState.audioCtx) return;
  if (CamState.audioCtx.state === 'suspended') {
    CamState.audioCtx.resume();
  }
  const now = CamState.audioCtx.currentTime;
  const osc = CamState.audioCtx.createOscillator();
  const gain = CamState.audioCtx.createGain();

  osc.type = 'triangle';
  osc.frequency.setValueAtTime(523.25, now);       // C5
  osc.frequency.setValueAtTime(659.25, now + 0.08); // E5
  osc.frequency.setValueAtTime(783.99, now + 0.16); // G5
  osc.frequency.setValueAtTime(1046.50, now + 0.24);// C6

  gain.gain.setValueAtTime(0.25, now);
  gain.gain.exponentialRampToValueAtTime(0.001, now + 0.65);

  osc.connect(gain);
  gain.connect(CamState.audioCtx.destination);
  osc.start(now);
  osc.stop(now + 0.65);

  if (navigator.vibrate) {
    navigator.vibrate([100, 50, 120]);
  }
}

async function loadMasterData() {
  try {
    const resp = await fetch('/api/data');
    if (!resp.ok) throw new Error("API-Fehler");
    const payload = await resp.json();
    CamState.platesData = payload.plates;
    CamState.tripCount = (payload.trip_log || []).length;
    document.getElementById('txtTripCount').textContent = CamState.tripCount;

    const map = {};
    payload.plates.forEach(p => {
      p.aliases.forEach(a => {
        map[a.toUpperCase().replace(/[- ]/g, '')] = p;
        map[a.toUpperCase()] = p;
      });
    });
    CamState.platesAliases = map;
    document.getElementById('txtConnection').textContent = "🟢 KI-Server verbunden";
  } catch (e) {
    console.warn("Konnte Daten nicht vorab laden, nutze Server-Abfragen", e);
    document.getElementById('txtConnection').textContent = "🟡 Verbindung wird aufgebaut...";
  }
}

async function startCamera() {
  const video = document.getElementById('videoFeed');
  try {
    if (CamState.stream) {
      CamState.stream.getTracks().forEach(t => t.stop());
    }

    const constraints = {
      video: {
        facingMode: { ideal: CamState.facing },
        width: { ideal: 1920, min: 1280 },
        height: { ideal: 1080, min: 720 }
      },
      audio: false
    };

    CamState.stream = await navigator.mediaDevices.getUserMedia(constraints);
    video.srcObject = CamState.stream;
    await video.play();

    // Verstecke die statische Box, wenn KI aktiv ist
    const targetWrapper = document.getElementById('targetWrapper');
    if (targetWrapper) {
      targetWrapper.style.opacity = '0.35';
    }
  } catch (err) {
    console.error("Kamerafehler:", err);
    document.getElementById('bannerTitle').textContent = "Kamerazugriff erforderlich";
    document.getElementById('bannerSub').textContent = "Bitte Kameraberechtigung im Browser erteilen";
  }
}

function switchCamera() {
  CamState.facing = CamState.facing === 'environment' ? 'user' : 'environment';
  startCamera();
}

// =====================================================================
// KI-ERKENNUNGSLOOP (YOLOv8 + ALPR AUF DEM SERVER)
// =====================================================================
async function startAiDetectionLoop() {
  while (true) {
    if (CamState.active && !CamState.isDetecting) {
      await processAiFrame();
    }
    // Flüssige Bildrate (ca. 2,5 - 3 Frames pro Sekunde für den KI-Server)
    await new Promise(r => setTimeout(r, 380));
  }
}

async function processAiFrame() {
  const video = document.getElementById('videoFeed');
  if (!video.videoWidth || video.paused || video.ended) return;

  CamState.isDetecting = true;

  try {
    const cropCanvas = document.getElementById('cropCanvas');
    const ctx = cropCanvas.getContext('2d');

    // Optimierte Bildgröße für High-Speed Inferenz
    const sendW = 960;
    const sendH = Math.round(960 * (video.videoHeight / video.videoWidth));
    cropCanvas.width = sendW;
    cropCanvas.height = sendH;
    ctx.drawImage(video, 0, 0, sendW, sendH);

    const frameB64 = cropCanvas.toDataURL('image/jpeg', 0.72);

    const resp = await fetch('/api/detect', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ image: frameB64 })
    });

    if (resp.ok) {
      const data = await resp.json();
      renderAiDetections(data.detections || []);
    }
  } catch (err) {
    console.warn("Inferenz-Zyklus:", err);
  } finally {
    CamState.isDetecting = false;
  }
}

// =====================================================================
// DYNAMISCHE BOUNDING-BOXEN AUF OVERLAY-CANVAS ZEICHNEN
// =====================================================================
function renderAiDetections(detections) {
  const overlayCanvas = document.getElementById('aiOverlayCanvas');
  const video = document.getElementById('videoFeed');
  if (!overlayCanvas || !video) return;

  const rect = video.getBoundingClientRect();
  overlayCanvas.width = rect.width;
  overlayCanvas.height = rect.height;

  const ctx = overlayCanvas.getContext('2d');
  ctx.clearRect(0, 0, overlayCanvas.width, overlayCanvas.height);

  if (!detections || detections.length === 0) {
    // Nach kurzer Zeit Banner zurücksetzen
    if (Date.now() - CamState.lastDetectionsTime > 3000) {
      const banner = document.getElementById('resultBanner');
      if (banner && !banner.classList.contains('banner-missing')) {
        document.getElementById('bannerTitle').textContent = "Scanne Straße...";
        document.getElementById('bannerSub').textContent = "YOLOv8 KI sucht nach Kennzeichen";
        document.getElementById('bannerLetters').textContent = "---";
        banner.className = 'result-banner';
      }
    }
    return;
  }

  CamState.lastDetectionsTime = Date.now();

  // Video Object-Fit: Cover Skalierung berechnen
  const vw = video.videoWidth;
  const vh = video.videoHeight;
  const cw = overlayCanvas.width;
  const ch = overlayCanvas.height;

  const scale = Math.max(cw / vw, ch / vh);
  const offsetX = (cw - vw * scale) / 2;
  const offsetY = (ch - vh * scale) / 2;

  detections.forEach(det => {
    const box = det.box;
    const conf = det.confidence;
    const plate = det.plate;
    const status = det.status || (plate ? plate.status : 'unknown');

    // Box Koordinaten auf dem Bildschirm
    const bx = (box.x * vw) * scale + offsetX;
    const by = (box.y * vh) * scale + offsetY;
    const bw = (box.w * vw) * scale;
    const bh = (box.h * vh) * scale;

    const isMissing = status === 'missing';
    const strokeColor = isMissing ? '#10b981' : (status === 'seen' ? '#3b82f6' : '#eab308');

    // Leuchtender KI-Rahmen um das Kennzeichen
    ctx.save();
    ctx.strokeStyle = strokeColor;
    ctx.lineWidth = Math.max(3, Math.round(cw * 0.006));
    ctx.shadowColor = strokeColor;
    ctx.shadowBlur = 12;

    // Abgerundetes Rechteck
    const radius = 6;
    ctx.beginPath();
    ctx.roundRect(bx, by, bw, bh, radius);
    ctx.stroke();

    // KI-Label Badge oberhalb der Bounding Box
    const codeText = det.matched_code || (plate ? plate.kuerzel : 'KENNZEICHEN');
    const badgeText = `${codeText} • ${Math.round(conf)}%`;
    ctx.font = `bold ${Math.max(13, Math.round(ch * 0.022))}px 'JetBrains Mono', monospace`;
    const textWidth = ctx.measureText(badgeText).width;
    const badgeHeight = Math.max(22, Math.round(ch * 0.035));
    const badgeWidth = textWidth + 16;
    const badgeY = Math.max(0, by - badgeHeight - 4);

    ctx.fillStyle = strokeColor;
    ctx.shadowBlur = 0;
    ctx.beginPath();
    ctx.roundRect(bx, badgeY, badgeWidth, badgeHeight, 5);
    ctx.fill();

    ctx.fillStyle = '#000000';
    ctx.textBaseline = 'middle';
    ctx.fillText(badgeText, bx + 8, badgeY + (badgeHeight / 2));
    ctx.restore();

    // Treffer verarbeiten & Trip erfassen
    if (plate && plate.kuerzel) {
      handlePlateMatch(plate, status, conf, box);
    }
  });
}

async function handlePlateMatch(plate, status, confidence, boxNorm) {
  const kuerzel = plate.kuerzel;
  const now = Date.now();

  // Cooldown-Prüfung: 15 Sekunden Pause für exakt dieses Kennzeichen
  if (CamState.lastCapturedPlates[kuerzel] && (now - CamState.lastCapturedPlates[kuerzel] < 15000)) {
    return;
  }
  CamState.lastCapturedPlates[kuerzel] = now;

  // Hochauflösendes Vollbild für Beweisfoto schnappen
  const video = document.getElementById('videoFeed');
  const fullCanvas = document.getElementById('fullCanvas');
  const fullCtx = fullCanvas.getContext('2d');
  fullCanvas.width = video.videoWidth;
  fullCanvas.height = video.videoHeight;
  fullCtx.drawImage(video, 0, 0);

  const fullImageB64 = fullCanvas.toDataURL('image/jpeg', 0.88);

  try {
    const resp = await fetch('/api/capture', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        query: kuerzel,
        image: fullImageB64,
        box: boxNorm,
        confidence: confidence,
        source: 'smartphone_ai'
      })
    });

    const result = await resp.json();
    if (result.success) {
      showDetectionFeedback(result.plate, result.status, result.saved_filename, confidence);
    }
  } catch (err) {
    console.error("Capture Upload Fehler:", err);
  }
}

function showDetectionFeedback(plate, status, filename, confidence) {
  const banner = document.getElementById('resultBanner');
  const lettersEl = document.getElementById('bannerLetters');
  const titleEl = document.getElementById('bannerTitle');
  const subEl = document.getElementById('bannerSub');
  const photoNotice = document.getElementById('bannerPhotoNotice');
  const flashOverlay = document.getElementById('flashOverlay');

  lettersEl.textContent = plate.kuerzel;

  if (status === 'missing') {
    // 🎉 NEUER FUND!
    banner.className = 'result-banner banner-missing';
    titleEl.textContent = `🎉 NEUER FUND: [${plate.kuerzel}] (+${plate.punkte} Pkt.)`;
    subEl.textContent = `${plate.stadt_kreis} (${plate.bundesland}) • KI: ${Math.round(confidence)}%`;
    photoNotice.style.display = 'flex';

    // Grüner Display-Flash & Ton & Vibration
    flashOverlay.classList.add('active');
    setTimeout(() => flashOverlay.classList.remove('active'), 280);
    playVictoryChime();

    // Trip Zähler erhöhen
    CamState.tripCount++;
    document.getElementById('txtTripCount').textContent = CamState.tripCount;
  } else {
    // Bereits gesehen
    banner.className = 'result-banner banner-seen';
    titleEl.textContent = `✓ [${plate.kuerzel}] Bereits in Sammlung`;
    subEl.textContent = `${plate.stadt_kreis} (${plate.bundesland}) • KI: ${Math.round(confidence)}%`;
    photoNotice.style.display = filename ? 'flex' : 'none';
  }
}

function initEventListeners() {
  document.getElementById('btnSwitchCam').addEventListener('click', switchCamera);

  document.getElementById('btnManualSnap').addEventListener('click', async () => {
    const bannerTitle = document.getElementById('bannerTitle');
    bannerTitle.textContent = "KI-Sofortscan...";
    await processAiFrame();
  });

  const btnPause = document.getElementById('btnPause');
  btnPause.addEventListener('click', () => {
    CamState.active = !CamState.active;
    document.getElementById('btnPauseIcon').textContent = CamState.active ? '⏸' : '▶';
    document.getElementById('btnPauseText').textContent = CamState.active ? 'Pause' : 'Weiter';
    document.getElementById('bannerTitle').textContent = CamState.active ? 'Scanne Straße...' : 'Pausiert';
  });
}
