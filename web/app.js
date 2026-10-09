/**
 * KENNZEICHEN RADAR • COCKPIT FRONTEND LOGIC
 * High-Speed Live-Sync, Minimalistisches Single-Screen Cockpit
 */

const Cockpit = {
  data: null,
  platesAliases: {},
  lastHitTime: null,
  soundEnabled: true,
  isPaused: false
};

document.addEventListener('DOMContentLoaded', async () => {
  initEventListeners();
  await loadInitialData();
  startStatusPollingLoop();
});

async function loadInitialData() {
  try {
    const res = await fetch('/api/data');
    if (!res.ok) return;
    const payload = await res.json();
    Cockpit.data = payload;
    updateSummary(payload.summary);
    renderTripList(payload.trip_log || []);

    // Alias map für Sofort-Suche
    const map = {};
    (payload.plates || []).forEach(p => {
      (p.aliases || []).forEach(a => {
        map[a.toUpperCase().replace(/[- ]/g, '')] = p;
        map[a.toUpperCase()] = p;
      });
    });
    Cockpit.platesAliases = map;
  } catch (err) {
    console.warn("Fehler beim Laden der Stammdaten:", err);
  }
}

function startStatusPollingLoop() {
  setInterval(async () => {
    try {
      const res = await fetch('/api/status');
      if (!res.ok) return;
      const status = await res.json();
      
      // 1. Live FPS & Cam
      if (status.fps !== undefined) {
        document.getElementById('fpsText').textContent = status.paused 
          ? "PAUSIERT" 
          : `LIVE • ${status.fps} FPS`;
      }

      // 2. Summary Counters
      if (status.summary) {
        updateSummary(status.summary);
      }

      // 3. Live Treffer Update
      if (status.last_hit) {
        updateLiveHit(status.last_hit);
      }

      // 4. Trip Count & List
      document.getElementById('statTrip').textContent = status.trip_count || 0;
      document.getElementById('tripCountCaption').textContent = status.trip_count || 0;

      // Wenn sich die Trip-Länge geändert hat, Liste nachladen
      const tripRes = await fetch('/api/data');
      if (tripRes.ok) {
        const full = await tripRes.json();
        renderTripList(full.trip_log || []);
      }
    } catch (e) {
      // Offline / Stream connection
    }
  }, 1200);
}

function updateSummary(summary) {
  if (!summary) return;
  document.getElementById('statMissing').textContent = summary.missing_de || 0;
  document.getElementById('statSeen').textContent = summary.seen_de || 0;
  document.getElementById('statPoints').textContent = (summary.points_earned || 0).toLocaleString();
  
  if (summary.backup_info && summary.backup_info.filename) {
    document.getElementById('hudBackupInfo').textContent = `${summary.backup_info.filename} (${summary.seen_de} DE)`;
  }
}

function updateLiveHit(hit) {
  const lettersEl = document.getElementById('hitLetters');
  const badgeEl = document.getElementById('hitStatusBadge');
  const cityEl = document.getElementById('hitCity');
  const subEl = document.getElementById('hitSub');
  const timeEl = document.getElementById('hitTime');

  const p = hit.plate || hit;
  lettersEl.textContent = p.kuerzel || '---';
  cityEl.textContent = `${p.stadt_kreis || ''} (${p.bundesland || ''})`;
  timeEl.textContent = hit.time || '';

  if (hit.status === 'missing') {
    badgeEl.className = 'hit-status-badge badge-missing-hit';
    badgeEl.textContent = `🎉 NEUER FUND! (+${p.punkte || 10} PKT.)`;
    subEl.textContent = `KI-Sicherheit: ${Math.round(hit.conf || 90)}% • Beweisfoto gesichert!`;
  } else {
    badgeEl.className = 'hit-status-badge badge-seen-hit';
    badgeEl.textContent = `✓ BEREITS GESAMMELT`;
    subEl.textContent = `KI-Sicherheit: ${Math.round(hit.conf || 90)}%`;
  }
}

function renderTripList(tripLog) {
  const listEl = document.getElementById('tripList');
  if (!tripLog || tripLog.length === 0) {
    listEl.innerHTML = `
      <div class="empty-trip-notice" id="emptyTripNotice">
        <span>🚗</span> Noch keine Kennzeichen auf dieser Fahrt entdeckt.<br>
        Erkannte Neufunde erscheinen automatisch hier mit Beweisfoto!
      </div>
    `;
    return;
  }

  // Neueste Funde zuerst
  const reversed = [...tripLog].reverse();
  listEl.innerHTML = reversed.map(item => `
    <div class="trip-row-item">
      <div class="item-left">
        ${item.image 
          ? `<a href="/captures/${item.image}" target="_blank" title="Foto vergrößern"><img src="/captures/${item.image}" class="item-thumb" /></a>` 
          : `<span style="font-size:1.1rem">🚗</span>`}
        <div>
          <div class="item-code">[${item.kuerzel}]</div>
          <div class="item-city">${item.stadt_kreis}</div>
        </div>
      </div>
      <div class="item-meta">
        <span class="item-points">+${item.punkte}P</span>
        <span class="item-time">${item.time}</span>
      </div>
    </div>
  `).join('');
}

function initEventListeners() {
  // Kamera-Dropdown
  document.getElementById('camSelect').addEventListener('change', async (e) => {
    const idx = parseInt(e.target.value, 10);
    try {
      await fetch('/api/camera/switch', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ index: idx })
      });
    } catch (err) {
      console.warn("Fehler beim Kamerawechsel:", err);
    }
  });

  // Sound-Button
  document.getElementById('btnSoundToggle').addEventListener('click', async () => {
    try {
      const res = await fetch('/api/camera/sound', { method: 'POST' });
      const data = await res.json();
      Cockpit.soundEnabled = data.sound;
      document.getElementById('btnSoundToggle').textContent = data.sound ? '🔔' : '🔕';
    } catch (e) {}
  });

  // Pause-Button
  document.getElementById('btnPauseToggle').addEventListener('click', async () => {
    try {
      const res = await fetch('/api/camera/toggle', { method: 'POST' });
      const data = await res.json();
      Cockpit.isPaused = data.paused;
      document.getElementById('btnPauseToggle').textContent = data.paused ? '▶' : '⏸';
    } catch (e) {}
  });

  // Schnelleingabe
  const qInput = document.getElementById('quickInput');
  const qFeedback = document.getElementById('quickFeedback');

  qInput.addEventListener('input', (e) => {
    const val = e.target.value.trim().toUpperCase().replace(/[- ]/g, '');
    if (!val) {
      qFeedback.textContent = "Tippe ein Kürzel für den Sofortabgleich";
      return;
    }

    const p = Cockpit.platesAliases[val];
    if (p) {
      const isSeen = p.status === 'seen';
      qFeedback.innerHTML = isSeen 
        ? `<span style="color:#60a5fa">✓ [${p.kuerzel}] ${p.stadt_kreis} (Bereits gesammelt)</span>` 
        : `<span style="color:#34d399">🎉 [${p.kuerzel}] ${p.stadt_kreis} (FEHLEND! +${p.punkte} Pkt.)</span>`;
    } else {
      qFeedback.textContent = `Unbekanntes Kürzel "${val}"`;
    }
  });

  const addCurrentPlate = async () => {
    const val = qInput.value.trim().toUpperCase();
    if (!val) return;
    try {
      const res = await fetch('/api/trip/add', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ query: val })
      });
      const data = await res.json();
      if (data.success) {
        updateLiveHit({ plate: data.plate, status: data.status, time: 'Jetzt', conf: 100 });
        qInput.value = '';
        qFeedback.textContent = `[${data.plate.kuerzel}] zum Trip hinzugefügt!`;
      }
    } catch (err) {}
  };

  qInput.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') addCurrentPlate();
  });

  document.getElementById('btnQuickAdd').addEventListener('click', addCurrentPlate);
}

function handleVideoError(img) {
  setTimeout(() => {
    img.src = '/video_feed?t=' + Date.now();
  }, 1500);
}
