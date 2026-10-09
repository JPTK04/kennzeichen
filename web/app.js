/**
 * KENNZEICHEN RADAR - FRONTEND APPLICATION LOGIC
 * Manages Live Scanner, OCR, Quick Check, Trip Log & Data Visualization.
 */

// Application State
const State = {
  data: null,
  activeTab: 'tab-scanner',
  activeView: 'missing', // 'missing' | 'seen' | 'all'
  selectedBundesland: 'ALL',
  searchQuery: '',
  sortOrder: 'points-desc',
  currentQuickPlate: null,
  
  // Camera & OCR
  cameraStream: null,
  cameraFacing: 'environment', // 'environment' | 'user'
  autoScanTimer: null,
  isScanning: false,
  tesseractWorker: null,
  
  // Audio
  audioCtx: null
};

// =====================================================================
// INITIALIZATION
// =====================================================================
document.addEventListener('DOMContentLoaded', () => {
  initAudio();
  initEventListeners();
  loadData();
});

function initAudio() {
  try {
    const AudioContext = window.AudioContext || window.webkitAudioContext;
    if (AudioContext) {
      State.audioCtx = new AudioContext();
    }
  } catch (e) {
    console.warn("Web Audio API nicht unterstützt", e);
  }
}

function playSuccessChime() {
  if (!State.audioCtx) return;
  if (State.audioCtx.state === 'suspended') {
    State.audioCtx.resume();
  }
  const now = State.audioCtx.currentTime;
  const osc = State.audioCtx.createOscillator();
  const gain = State.audioCtx.createGain();

  osc.type = 'triangle';
  // Melodischer Dreiklang
  osc.frequency.setValueAtTime(523.25, now);       // C5
  osc.frequency.setValueAtTime(659.25, now + 0.1); // E5
  osc.frequency.setValueAtTime(783.99, now + 0.2); // G5
  osc.frequency.setValueAtTime(1046.50, now + 0.3);// C6

  gain.gain.setValueAtTime(0.15, now);
  gain.gain.exponentialRampToValueAtTime(0.001, now + 0.6);

  osc.connect(gain);
  gain.connect(State.audioCtx.destination);
  osc.start(now);
  osc.stop(now + 0.6);

  // Haptisches Feedback falls auf Smartphone
  if (navigator.vibrate) {
    navigator.vibrate([80, 40, 80]);
  }
}

// =====================================================================
// DATA LOADING & RENDERING
// =====================================================================
async function loadData() {
  try {
    const resp = await fetch('/api/data');
    if (!resp.ok) throw new Error("Fehler beim Laden der API-Daten");
    const payload = await resp.json();
    State.data = payload;

    renderKPIs(payload.summary);
    renderPill(payload.summary);
    renderBundeslandChips(payload.bundeslaender);
    renderBundeslandGrid(payload.bundeslaender);
    renderPlatesGrid();
    renderTripLog(payload.trip_log);

    // Initialen Quick-Check mit HVL als Demonstration
    const quickInput = document.getElementById('quickInput');
    if (!quickInput.value) {
      quickInput.value = 'HVL';
      handleQuickCheck('HVL');
    }
  } catch (err) {
    console.error("Data load error:", err);
    showToast("Fehler beim Laden der Daten", "warn");
  }
}

function renderKPIs(summary) {
  document.getElementById('kpiMissingCount').textContent = summary.missing_de;
  document.getElementById('kpiMissingSub').textContent = `von ${summary.total_de} Kennzeichen`;
  
  document.getElementById('kpiSeenCount').textContent = summary.seen_de;
  document.getElementById('kpiSeenPercent').textContent = `${summary.percent_seen} % gesammelt`;

  document.getElementById('kpiPointsCount').textContent = summary.points_earned.toLocaleString('de-DE');
  document.getElementById('kpiPointsTotal').textContent = `von ${summary.points_possible.toLocaleString('de-DE')} Pkt.`;

  // Counters in Tabs
  document.getElementById('tabCounterMissing').textContent = summary.missing_de;
  document.getElementById('countViewMissing').textContent = summary.missing_de;
  document.getElementById('countViewSeen').textContent = summary.seen_de;
}

function renderPill(summary) {
  const info = summary.backup_info;
  const pillName = document.getElementById('pillBackupName');
  const pillCount = document.getElementById('pillSeenCount');

  pillName.textContent = info.filename || "Backup geladen";
  pillCount.textContent = `${summary.seen_de} / ${summary.total_de}`;
  
  // Info Panel in Tab Backup
  document.getElementById('infoFileName').textContent = info.filename || "-";
  document.getElementById('infoTotalSeen').textContent = info.seen_count || "-";
  document.getElementById('infoDeSeen').textContent = summary.seen_de || "-";
  document.getElementById('infoLatestDate').textContent = info.date || "-";
}

// =====================================================================
// QUICK CHECK & PLATE DISPLAY
// =====================================================================
function handleQuickCheck(query) {
  const norm = query.trim().toUpperCase();
  const plateLettersEl = document.getElementById('plateLetters');
  plateLettersEl.textContent = norm || "???";

  if (!norm || !State.data) {
    resetStatusCard();
    return;
  }

  // 1. Suche nach exaktem Match unter allen Kennzeichen oder Aliases
  let matchedPlate = null;
  const cleanKey = norm.replace(/-/g, '').replace(/\s+/g, '');

  for (const p of State.data.plates) {
    // Check aliases
    const found = p.aliases.some(a => {
      const aClean = a.toUpperCase().replace(/-/g, '').replace(/\s+/g, '');
      return aClean === cleanKey || a.toUpperCase() === norm;
    });
    if (found) {
      matchedPlate = p;
      break;
    }
  }

  // Fallback: Falls der Benutzer etwas wie 'B AB 123' getippt/gescannt hat:
  if (!matchedPlate && norm.length >= 1) {
    const prefixMatch = norm.match(/^([A-ZÄÖÜ]{1,3})/);
    if (prefixMatch) {
      const prefix = prefixMatch[1];
      for (const p of State.data.plates) {
        if (p.aliases.some(a => a.toUpperCase() === prefix)) {
          matchedPlate = p;
          break;
        }
      }
    }
  }

  updateStatusCard(matchedPlate, norm);
}

function updateStatusCard(plate, query) {
  const card = document.getElementById('statusResultCard');
  const badge = document.getElementById('statusBadge');
  const title = document.getElementById('statusTitle');
  const desc = document.getElementById('statusDesc');
  const details = document.getElementById('statusDetails');
  const actions = document.getElementById('statusActions');

  card.className = 'status-result-card'; // reset classes

  if (!plate) {
    card.classList.add('state-unknown');
    badge.textContent = 'Unbekannt';
    title.textContent = `"${query}" nicht gefunden`;
    desc.textContent = 'Dieses Kürzel existiert nicht in den deutschen Kennzeichen-Stammdaten.';
    details.style.display = 'none';
    actions.style.display = 'none';
    State.currentQuickPlate = null;
    return;
  }

  State.currentQuickPlate = plate;
  details.style.display = 'flex';
  actions.style.display = 'block';

  document.getElementById('detailOrt').textContent = plate.stadt_kreis;
  document.getElementById('detailBundesland').textContent = plate.bundesland;
  document.getElementById('detailPunkte').textContent = `${plate.punkte} Punkte`;

  if (plate.status === 'missing') {
    card.classList.add('state-missing');
    badge.textContent = '🎉 Noch fehlend!';
    title.textContent = `Kürzel [${plate.kuerzel}] fehlt dir noch!`;
    desc.textContent = `Super Fund! ${plate.stadt_kreis} (${plate.bundesland}) bringt dir +${plate.punkte} Punkte.`;
  } else {
    card.classList.add('state-seen');
    badge.textContent = '✓ Bereits gesehen';
    title.textContent = `Kürzel [${plate.kuerzel}] ist schon in deiner Sammlung`;
    desc.textContent = `Gesehen am ${plate.seen_date || 'Unbekanntes Datum'} in ${plate.stadt_kreis}.`;
  }
}

function resetStatusCard() {
  const card = document.getElementById('statusResultCard');
  card.className = 'status-result-card';
  document.getElementById('statusBadge').textContent = 'Bereit';
  document.getElementById('statusTitle').textContent = 'Tippe ein Kürzel ein';
  document.getElementById('statusDesc').textContent = 'Tippe oben ein Kennzeichen-Kürzel ein oder nutze den Kamera-Scanner.';
  document.getElementById('statusDetails').style.display = 'none';
  document.getElementById('statusActions').style.display = 'none';
  State.currentQuickPlate = null;
}

// =====================================================================
// TRIP LOG MANAGEMENT
// =====================================================================
async function addToTrip(plate, source = 'manuell') {
  if (!plate) return;

  const now = new Date();
  const timeStr = now.toLocaleTimeString('de-DE', { hour: '2-digit', minute: '2-digit' });

  try {
    const resp = await fetch('/api/trip/add', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ id: plate.id, source, time: timeStr })
    });
    const payload = await resp.json();
    if (payload.success) {
      renderTripLog(payload.trip_log);
      playSuccessChime();
      showToast(`[${plate.kuerzel}] zur Trip-Merkliste hinzugefügt!`, 'success');
    }
  } catch (err) {
    console.error("Trip add error:", err);
  }
}

async function removeFromTrip(id) {
  try {
    const resp = await fetch('/api/trip/remove', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ id })
    });
    const payload = await resp.json();
    if (payload.success) {
      renderTripLog(payload.trip_log);
      showToast("Eintrag aus Trip entfernt", "info");
    }
  } catch (err) {
    console.error("Trip remove error:", err);
  }
}

async function clearTrip() {
  if (!confirm("Möchtest du die Trip-Merkliste wirklich leeren?")) return;
  try {
    const resp = await fetch('/api/trip/clear', { method: 'POST' });
    const payload = await resp.json();
    if (payload.success) {
      renderTripLog([]);
      showToast("Trip-Merkliste geleert", "info");
    }
  } catch (err) {
    console.error("Trip clear error:", err);
  }
}

function renderTripLog(trip) {
  State.data.trip_log = trip;
  const tbody = document.getElementById('tripTableBody');
  const emptyMsg = document.getElementById('emptyTripMessage');
  const countEl = document.getElementById('kpiTripCount');
  const tabBadgeEl = document.getElementById('tabCounterTrip');
  const totalPlatesEl = document.getElementById('tripTotalPlates');
  const totalPointsEl = document.getElementById('tripTotalPoints');

  countEl.textContent = trip.length;
  tabBadgeEl.textContent = trip.length;
  totalPlatesEl.textContent = trip.length;

  const sumPoints = trip.reduce((acc, item) => acc + (item.punkte || 10), 0);
  totalPointsEl.textContent = `+${sumPoints} Pkt.`;

  tbody.innerHTML = '';
  if (!trip || trip.length === 0) {
    emptyMsg.style.display = 'block';
    return;
  }
  emptyMsg.style.display = 'none';

  trip.slice().reverse().forEach(item => {
    const tr = document.createElement('tr');
    const photoHtml = item.image
      ? `<div class="trip-thumb-wrapper"><img src="/captures/${escapeHtml(item.image)}" class="trip-thumb" onclick="openPhotoModal('/captures/${escapeHtml(item.image)}', '[${escapeHtml(item.kuerzel)}] ${escapeHtml(item.stadt_kreis)}')" title="Klicken für Vollbild"></div>`
      : `<span class="no-photo-badge">Manuell</span>`;

    tr.innerHTML = `
      <td style="text-align: center;">${photoHtml}</td>
      <td><strong>${escapeHtml(item.kuerzel)}</strong></td>
      <td>${escapeHtml(item.stadt_kreis)}</td>
      <td><span class="status-tag seen">${escapeHtml(item.bundesland)}</span></td>
      <td><span class="points-badge">+${item.punkte || 10}</span></td>
      <td style="color: var(--text-dim);">${item.time || '-'}</td>
      <td>
        <button class="btn-danger" style="padding: 0.2rem 0.5rem; font-size: 0.75rem;" onclick="removeFromTrip(${item.id})">
          ✕
        </button>
      </td>
    `;
    tbody.appendChild(tr);
  });
}

function copyTripForApp() {
  const trip = State.data.trip_log;
  if (!trip || trip.length === 0) {
    showToast("Trip-Liste ist leer", "warn");
    return;
  }

  // Formatierte Liste zum einfachen Abhaken in der Android App
  let text = "🚗 MEINE GESEHENEN KENNZEICHEN VOM ROADTRIP:\n";
  text += "========================================\n";
  trip.forEach((t, i) => {
    text += `${i + 1}. [${t.kuerzel}] - ${t.stadt_kreis} (${t.bundesland})\n`;
  });
  text += "\nIn die Kennzeichensammler-App übertragen und abhaken!";

  navigator.clipboard.writeText(text).then(() => {
    showToast("📋 Liste in Zwischenablage kopiert!", "success");
  }).catch(() => {
    showToast("Kopieren fehlgeschlagen", "warn");
  });
}

// =====================================================================
// PLATES GRID & FILTERING
// =====================================================================
function renderBundeslandChips(blStats) {
  const container = document.getElementById('bundeslandChipsContainer');
  container.innerHTML = '';

  // All Chip
  const allChip = document.createElement('button');
  allChip.className = `bl-chip ${State.selectedBundesland === 'ALL' ? 'active' : ''}`;
  allChip.innerHTML = `<span>Alle Länder</span>`;
  allChip.onclick = () => {
    State.selectedBundesland = 'ALL';
    updateChipsActive();
    renderPlatesGrid();
  };
  container.appendChild(allChip);

  // Sorted Bundesländer
  const sortedBL = Object.keys(blStats).sort();
  sortedBL.forEach(bl => {
    const chip = document.createElement('button');
    chip.className = `bl-chip ${State.selectedBundesland === bl ? 'active' : ''}`;
    chip.dataset.bl = bl;
    const missing = blStats[bl].missing;
    chip.innerHTML = `<span>${escapeHtml(bl)}</span> <span class="bl-chip-count">${missing}</span>`;
    chip.onclick = () => {
      State.selectedBundesland = bl;
      updateChipsActive();
      renderPlatesGrid();
    };
    container.appendChild(chip);
  });
}

function updateChipsActive() {
  const chips = document.querySelectorAll('.bl-chip');
  chips.forEach(c => {
    if ((c.dataset.bl || 'ALL') === State.selectedBundesland) {
      c.classList.add('active');
    } else {
      c.classList.remove('active');
    }
  });
}

function renderPlatesGrid() {
  if (!State.data) return;
  const grid = document.getElementById('platesGridContainer');
  const emptyState = document.getElementById('platesEmptyState');
  grid.innerHTML = '';

  const q = State.searchQuery.trim().toLowerCase();

  let filtered = State.data.plates.filter(p => {
    // 1. View Filter (missing / seen / all)
    if (State.activeView === 'missing' && p.status !== 'missing') return false;
    if (State.activeView === 'seen' && p.status !== 'seen') return false;

    // 2. Bundesland Filter
    if (State.selectedBundesland !== 'ALL' && p.bundesland !== State.selectedBundesland) return false;

    // 3. Search Query
    if (q) {
      const matchKuerzel = p.kuerzel.toLowerCase().includes(q) || p.aliases.some(a => a.toLowerCase().includes(q));
      const matchOrt = p.stadt_kreis.toLowerCase().includes(q);
      const matchAbleitung = p.ableitung.toLowerCase().includes(q);
      const matchBL = p.bundesland.toLowerCase().includes(q);
      if (!matchKuerzel && !matchOrt && !matchAbleitung && !matchBL) return false;
    }

    return true;
  });

  // Sorting
  if (State.sortOrder === 'points-desc') {
    filtered.sort((a, b) => (b.punkte || 0) - (a.punkte || 0));
  } else if (State.sortOrder === 'points-asc') {
    filtered.sort((a, b) => (a.punkte || 0) - (b.punkte || 0));
  } else if (State.sortOrder === 'alpha-asc') {
    filtered.sort((a, b) => a.kuerzel.localeCompare(b.kuerzel));
  } else if (State.sortOrder === 'bundesland') {
    filtered.sort((a, b) => a.bundesland.localeCompare(b.bundesland));
  }

  if (filtered.length === 0) {
    emptyState.style.display = 'block';
    return;
  }
  emptyState.style.display = 'none';

  // Render cards (limit to first 120 for instant DOM performance, lazy load on scroll if needed)
  const renderLimit = 120;
  const toRender = filtered.slice(0, renderLimit);

  toRender.forEach(p => {
    const card = document.createElement('div');
    card.className = `plate-card ${p.status === 'seen' ? 'card-seen' : 'card-missing'}`;
    
    card.innerHTML = `
      <div class="plate-card-top">
        <div class="mini-plate">
          <span class="mini-eu">D</span>
          <span class="mini-letters">${escapeHtml(p.kuerzel)}</span>
        </div>
        <span class="points-badge">+${p.punkte || 10} Pkt.</span>
      </div>
      <div class="plate-card-body">
        <h4>${escapeHtml(p.stadt_kreis)}</h4>
        <div class="plate-card-sub">${escapeHtml(p.bundesland)}${p.ableitung ? ` • ${escapeHtml(p.ableitung)}` : ''}</div>
      </div>
      <div class="plate-card-footer">
        <span class="status-tag ${p.status}">
          ${p.status === 'missing' ? 'FEHLT NOCH' : `GESEHEN (${p.seen_date || ''})`}
        </span>
        <button class="btn-card-add" title="Auf Merkliste setzen">
          ➕ Trip
        </button>
      </div>
    `;

    card.querySelector('.btn-card-add').onclick = () => {
      addToTrip(p, 'liste');
    };

    grid.appendChild(card);
  });

  if (filtered.length > renderLimit) {
    const moreNotice = document.createElement('div');
    moreNotice.style.gridColumn = "1 / -1";
    moreNotice.style.textAlign = "center";
    moreNotice.style.padding = "1rem";
    moreNotice.style.color = "var(--text-muted)";
    moreNotice.textContent = `Zeige ${renderLimit} von ${filtered.length} Kennzeichen. Nutze die Suche oder Bundesland-Filter, um die Auswahl einzugrenzen.`;
    grid.appendChild(moreNotice);
  }
}

function renderBundeslandGrid(blStats) {
  const grid = document.getElementById('bundeslandGrid');
  grid.innerHTML = '';

  const sortedBL = Object.keys(blStats).sort();
  sortedBL.forEach(bl => {
    const item = blStats[bl];
    const pct = item.total > 0 ? Math.round((item.seen / item.total) * 100) : 0;

    const card = document.createElement('div');
    card.className = 'bl-card';
    card.innerHTML = `
      <div class="bl-card-title">
        <span class="bl-card-name">${escapeHtml(bl)}</span>
        <span class="bl-card-percent">${pct}%</span>
      </div>
      <div class="bl-progress-track">
        <div class="bl-progress-fill" style="width: ${pct}%;"></div>
      </div>
      <div class="bl-card-stats">
        <span>Gesehen: ${item.seen} / ${item.total}</span>
        <span style="color: var(--accent-amber); font-weight: 700;">Fehlen: ${item.missing}</span>
      </div>
    `;

    // Klick springt direkt in Tab "Fehlende Liste" mit diesem Bundesland gefiltert!
    card.onclick = () => {
      State.selectedBundesland = bl;
      State.activeView = 'missing';
      updateChipsActive();
      document.querySelectorAll('.toggle-btn').forEach(b => b.classList.remove('active'));
      document.getElementById('btnViewMissing').classList.add('active');
      renderPlatesGrid();
      switchTab('tab-missing');
    };

    grid.appendChild(card);
  });
}

// =====================================================================
// CAMERA SCANNER & OCR
// =====================================================================
async function startCamera() {
  const video = document.getElementById('cameraVideo');
  const placeholder = document.getElementById('cameraPlaceholder');
  const btnToggle = document.getElementById('btnToggleCamera');
  const btnSwitch = document.getElementById('btnSwitchCamera');
  const btnSnap = document.getElementById('btnSnapScan');

  try {
    const constraints = {
      video: {
        facingMode: { ideal: State.cameraFacing },
        width: { ideal: 1280 },
        height: { ideal: 720 }
      }
    };
    State.cameraStream = await navigator.mediaDevices.getUserMedia(constraints);
    video.srcObject = State.cameraStream;
    await video.play();

    placeholder.style.display = 'none';
    btnToggle.style.display = 'flex';
    btnSwitch.style.display = 'flex';
    btnSnap.style.display = 'flex';
    showToast("Kamera gestartet", "info");
  } catch (err) {
    console.error("Camera access error:", err);
    showToast("Kamera-Zugriff verweigert oder nicht verfügbar", "warn");
  }
}

function stopCamera() {
  if (State.cameraStream) {
    State.cameraStream.getTracks().forEach(t => t.stop());
    State.cameraStream = null;
  }
  if (State.autoScanTimer) {
    clearInterval(State.autoScanTimer);
    State.autoScanTimer = null;
    document.getElementById('chkAutoScan').checked = false;
  }

  document.getElementById('cameraPlaceholder').style.display = 'flex';
  document.getElementById('btnToggleCamera').style.display = 'none';
  document.getElementById('btnSwitchCamera').style.display = 'none';
  document.getElementById('btnSnapScan').style.display = 'none';
}

function switchCamera() {
  State.cameraFacing = State.cameraFacing === 'environment' ? 'user' : 'environment';
  stopCamera();
  startCamera();
}

async function performOCRScan() {
  if (State.isScanning) return;
  const video = document.getElementById('cameraVideo');
  if (!video.videoWidth || video.paused) return;

  State.isScanning = true;
  const overlay = document.getElementById('ocrOverlay');
  const statusText = document.getElementById('ocrStatusText');
  overlay.style.display = 'flex';
  statusText.textContent = "Erkenne Kennzeichen...";

  try {
    const canvas = document.getElementById('cameraCanvas');
    const ctx = canvas.getContext('2d');
    
    // Wir croppen das Zentrum des Bildes (Guide-Rechteck)
    const vw = video.videoWidth;
    const vh = video.videoHeight;
    const cropW = Math.round(vw * 0.75);
    const cropH = Math.round(vh * 0.35);
    const cropX = Math.round((vw - cropW) / 2);
    const cropY = Math.round((vh - cropH) / 2);

    canvas.width = cropW;
    canvas.height = cropH;
    ctx.drawImage(video, cropX, cropY, cropW, cropH, 0, 0, cropW, cropH);

    // Bild-Vorverarbeitung: Kontrastverstärkung für Schrifterkennung
    const imgData = ctx.getImageData(0, 0, cropW, cropH);
    const d = imgData.data;
    for (let i = 0; i < d.length; i += 4) {
      // Grayscale
      const gray = 0.299 * d[i] + 0.587 * d[i+1] + 0.114 * d[i+2];
      // Kontrastspreizung
      const v = gray > 140 ? 255 : (gray < 80 ? 0 : gray);
      d[i] = v; d[i+1] = v; d[i+2] = v;
    }
    ctx.putImageData(imgData, 0, 0);

    // OCR mit Tesseract.js (falls verfügbar)
    if (window.Tesseract) {
      const result = await Tesseract.recognize(canvas, 'eng+deu', {
        logger: m => {}
      });
      const rawText = result.data.text.toUpperCase();
      console.log("OCR raw result:", rawText);

      // Regex für deutsches Kennzeichenkürzel (z. B. 'B AB 123' oder 'HVL' oder 'ESB')
      const cleaned = rawText.replace(/[^A-ZÄÖÜ0-9]/g, ' ').trim();
      const tokens = cleaned.split(/\s+/).filter(t => t.length >= 1 && t.length <= 3 && /^[A-ZÄÖÜ]+$/.test(t));
      
      let candidate = null;
      for (const tok of tokens) {
        // Prüfen ob es ein deutsches Kennzeichen ist
        if (State.data.plates.some(p => p.aliases.some(a => a.toUpperCase() === tok))) {
          candidate = tok;
          break;
        }
      }

      if (candidate) {
        document.getElementById('quickInput').value = candidate;
        handleQuickCheck(candidate);
        showToast(`Kennzeichen [${candidate}] erkannt!`, "success");

        // Automatische Übernahme in Trip-Merkliste, wenn fehlend
        const chkAuto = document.getElementById('chkAutoCollect');
        if (chkAuto && chkAuto.checked && State.currentQuickPlate && State.currentQuickPlate.status === 'missing') {
          const alreadyInTrip = State.data.trip_log && State.data.trip_log.some(t => t.id === State.currentQuickPlate.id);
          if (!alreadyInTrip) {
            addToTrip(State.currentQuickPlate, 'kamera_auto');
          }
        }
      } else {
        showToast("Kein eindeutiges Kennzeichen erkannt", "info");
      }
    } else {
      showToast("Tesseract OCR Modul lädt noch...", "info");
    }
  } catch (err) {
    console.error("Scan error:", err);
  } finally {
    overlay.style.display = 'none';
    State.isScanning = false;
  }
}

// =====================================================================
// BACKUP UPLOADER
// =====================================================================
async function uploadBackupFile(file) {
  if (!file) return;
  showToast(`Lade Backup "${file.name}"...`, "info");

  try {
    const formData = new FormData();
    formData.append("file", file);

    const resp = await fetch('/api/upload', {
      method: 'POST',
      headers: {
        'X-Filename': encodeURIComponent(file.name)
      },
      body: file // Direct binary body
    });

    const payload = await resp.json();
    if (payload.success) {
      State.data = payload.data;
      renderKPIs(payload.data.summary);
      renderPill(payload.data.summary);
      renderBundeslandChips(payload.data.bundeslaender);
      renderBundeslandGrid(payload.data.bundeslaender);
      renderPlatesGrid();
      showToast(`Backup "${file.name}" erfolgreich eingespielt!`, "success");
      switchTab('tab-scanner');
    } else {
      showToast("Backup-Fehler: " + payload.error, "warn");
    }
  } catch (err) {
    console.error("Upload error:", err);
    showToast("Upload fehlgeschlagen", "warn");
  }
}

// =====================================================================
// EVENT LISTENERS & NAVIGATION
// =====================================================================
function initEventListeners() {
  // Navigation Tabs
  document.querySelectorAll('.nav-tab').forEach(tab => {
    tab.addEventListener('click', () => {
      switchTab(tab.dataset.tab);
    });
  });

  // Backup Status Pill Shortcut
  document.getElementById('backupStatusPill').addEventListener('click', () => {
    switchTab('tab-backup');
  });

  // Quick Input
  const quickInput = document.getElementById('quickInput');
  quickInput.addEventListener('input', (e) => {
    handleQuickCheck(e.target.value);
  });

  quickInput.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') {
      if (State.currentQuickPlate) {
        addToTrip(State.currentQuickPlate, 'schnelleingabe');
      }
    }
  });

  document.getElementById('btnClearInput').addEventListener('click', () => {
    quickInput.value = '';
    handleQuickCheck('');
    quickInput.focus();
  });

  document.getElementById('btnAddToTrip').addEventListener('click', () => {
    if (State.currentQuickPlate) {
      addToTrip(State.currentQuickPlate, 'button');
    }
  });

  // Camera Controls
  document.getElementById('btnStartCamera').addEventListener('click', startCamera);
  document.getElementById('btnToggleCamera').addEventListener('click', stopCamera);
  document.getElementById('btnSwitchCamera').addEventListener('click', switchCamera);
  document.getElementById('btnSnapScan').addEventListener('click', performOCRScan);

  document.getElementById('chkAutoScan').addEventListener('change', (e) => {
    if (e.target.checked) {
      if (!State.cameraStream) {
        startCamera().then(() => {
          State.autoScanTimer = setInterval(performOCRScan, 1500);
        });
      } else {
        State.autoScanTimer = setInterval(performOCRScan, 1500);
      }
    } else {
      if (State.autoScanTimer) {
        clearInterval(State.autoScanTimer);
        State.autoScanTimer = null;
      }
    }
  });

  document.getElementById('filePhotoInput').addEventListener('change', (e) => {
    const file = e.target.files[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = async (evt) => {
      const img = new Image();
      img.onload = async () => {
        const canvas = document.getElementById('cameraCanvas');
        const ctx = canvas.getContext('2d');
        canvas.width = img.width;
        canvas.height = img.height;
        ctx.drawImage(img, 0, 0);

        if (window.Tesseract) {
          showToast("Scanne hochgeladenes Foto...", "info");
          const res = await Tesseract.recognize(canvas, 'eng+deu');
          const raw = res.data.text.toUpperCase();
          const tokens = raw.replace(/[^A-ZÄÖÜ]/g, ' ').split(/\s+/).filter(t => t.length >= 1 && t.length <= 3);
          for (const tok of tokens) {
            if (State.data.plates.some(p => p.aliases.some(a => a.toUpperCase() === tok))) {
              quickInput.value = tok;
              handleQuickCheck(tok);
              showToast(`Gefundenes Kennzeichen: [${tok}]`, "success");
              return;
            }
          }
          showToast("Kein bekanntes Kennzeichen auf dem Foto gefunden", "warn");
        }
      };
      img.src = evt.target.result;
    };
    reader.readAsDataURL(file);
  });

  // Filter Buttons
  document.getElementById('btnViewMissing').addEventListener('click', () => {
    setViewToggle('missing');
  });
  document.getElementById('btnViewSeen').addEventListener('click', () => {
    setViewToggle('seen');
  });
  document.getElementById('btnViewAll').addEventListener('click', () => {
    setViewToggle('all');
  });

  document.getElementById('filterSearchInput').addEventListener('input', (e) => {
    State.searchQuery = e.target.value;
    renderPlatesGrid();
  });

  document.getElementById('sortSelect').addEventListener('change', (e) => {
    State.sortOrder = e.target.value;
    renderPlatesGrid();
  });

  // Trip Log Actions
  document.getElementById('btnCopyForApp').addEventListener('click', copyTripForApp);
  document.getElementById('btnExportTripCsv').addEventListener('click', () => {
    window.location.href = '/api/trip/export';
  });
  document.getElementById('btnClearTrip').addEventListener('click', clearTrip);

  // Backup Drag & Drop
  const dropZone = document.getElementById('dropZone');
  const fileBackupInput = document.getElementById('fileBackupInput');
  const btnSelectBackup = document.getElementById('btnSelectBackup');

  btnSelectBackup.addEventListener('click', () => fileBackupInput.click());
  fileBackupInput.addEventListener('change', (e) => {
    if (e.target.files[0]) uploadBackupFile(e.target.files[0]);
  });

  dropZone.addEventListener('dragover', (e) => {
    e.preventDefault();
    dropZone.classList.add('dragover');
  });
  dropZone.addEventListener('dragleave', () => {
    dropZone.classList.remove('dragover');
  });
  dropZone.addEventListener('drop', (e) => {
    e.preventDefault();
    dropZone.classList.remove('dragover');
    if (e.dataTransfer.files[0]) {
      uploadBackupFile(e.dataTransfer.files[0]);
    }
  });
}

function switchTab(tabId) {
  State.activeTab = tabId;
  document.querySelectorAll('.nav-tab').forEach(t => {
    if (t.dataset.tab === tabId) t.classList.add('active');
    else t.classList.remove('active');
  });

  document.querySelectorAll('.tab-content').forEach(c => {
    if (c.id === tabId) c.classList.add('active');
    else c.classList.remove('active');
  });
}

function setViewToggle(view) {
  State.activeView = view;
  document.querySelectorAll('.toggle-btn').forEach(b => {
    if (b.dataset.view === view) b.classList.add('active');
    else b.classList.remove('active');
  });
  renderPlatesGrid();
}

// Toast helper
function showToast(msg, type = 'info') {
  const container = document.getElementById('toastContainer');
  const toast = document.createElement('div');
  toast.className = `toast ${type}`;
  toast.innerHTML = `<span>${type === 'success' ? '✓' : (type === 'warn' ? '⚠' : 'ℹ')}</span> <span>${escapeHtml(msg)}</span>`;
  container.appendChild(toast);

  setTimeout(() => {
    toast.style.opacity = '0';
    toast.style.transform = 'translateX(100%)';
    toast.style.transition = 'all 0.3s ease';
    setTimeout(() => toast.remove(), 300);
  }, 3500);
}

function escapeHtml(str) {
  if (!str) return '';
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

// =====================================================================
// PHOTO LIGHTBOX MODAL & LIVE AUTO-SYNC
// =====================================================================
function openPhotoModal(src, caption) {
  const modal = document.getElementById('photoModal');
  const img = document.getElementById('modalImg');
  const cap = document.getElementById('modalCaption');
  img.src = src;
  cap.textContent = caption || '';
  modal.style.display = 'flex';
}

function closePhotoModal() {
  const modal = document.getElementById('photoModal');
  if (modal) modal.style.display = 'none';
}

document.getElementById('btnModalClose').addEventListener('click', closePhotoModal);
document.getElementById('modalBackdrop').addEventListener('click', closePhotoModal);

// Auto-Sync Polling alle 2 Sekunden: Hält den Laptop live synchron mit dem Smartphone!
setInterval(async () => {
  try {
    const resp = await fetch('/api/data');
    if (resp.ok) {
      const payload = await resp.json();
      const prevTripLen = State.data ? (State.data.trip_log || []).length : 0;
      const newTripLen = (payload.trip_log || []).length;
      if (newTripLen !== prevTripLen) {
        State.data = payload;
        renderKPIs(payload.summary);
        renderTripLog(payload.trip_log);
        renderPlatesGrid();
        playSuccessChime();
        showToast(`🚗 Neues Kennzeichen vom Smartphone erfasst! (${newTripLen} heute)`, 'success');
      }
    }
  } catch(e) {}
}, 2000);
