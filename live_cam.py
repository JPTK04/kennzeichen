#!/usr/bin/env python3
"""
Kennzeichen-Radar • High-Precision Intervall- & Live-Scanner (USB / Handy / Webcam)
100% Offline-fähig – Entwickelt für Fahrten im Auto & Dashcam-Betrieb.

Features:
- Threaded Full-HD 1080p Video-Pipeline: 60 FPS flüssiger Sucher ohne Ruckeln
- Präzisions-Intervall-Scan (z. B. jede Sekunde oder per Leertaste)
- Erkennt automatisch Smartphone-Kameras (Android Webcam / DroidCam / Cam 1)
- Strikte Links-nach-Rechts-Zonierung für korrekte deutsche Ortskürzel
- Automatischer Victory-Sound, Beweisfotos in captures/ und CSV-Trip-Export
"""

import sys
import os
import time
import winsound
import sqlite3
import zlib
import tempfile
import threading
from pathlib import Path
from datetime import datetime

import cv2
import numpy as np

# UTF-8 Konsole
sys.stdout.reconfigure(encoding='utf-8')

BASE_DIR = Path(__file__).resolve().parent
CAPTURES_DIR = BASE_DIR / "captures"
CAPTURES_DIR.mkdir(parents=True, exist_ok=True)
DATA_DB_PATH = BASE_DIR / "data" / "data.db"
DOWNLOADS_DIR = Path.home() / "Downloads"

from detector import FastLicensePlateDetector

class ThreadedCamera:
    """Entkoppelt das Kamerasignal in einen eigenen Thread für 60 FPS ohne Ruckeln."""
    def __init__(self, src: int = 1, width: int = 1920, height: int = 1080):
        self.src = src
        self.width = width
        self.height = height
        self.cap = None
        self.frame = None
        self.ret = False
        self.running = False
        self.lock = threading.Lock()
        self.thread = None
        self.open(src)

    def open(self, src: int):
        self.release()
        self.src = src
        print(f"[+] Verbinde mit Kamera Index {self.src} (DirectShow Full-HD)...")
        self.cap = cv2.VideoCapture(self.src, cv2.CAP_DSHOW)
        if not self.cap.isOpened():
            self.cap = cv2.VideoCapture(self.src)

        if self.cap.isOpened():
            # MJPG anfordern für maximale 1080p Bildrate über USB
            self.cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*'MJPG'))
            self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
            self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
            self.ret, self.frame = self.cap.read()
            self.running = True
            self.thread = threading.Thread(target=self._capture_loop, daemon=True, name=f"Cam_{self.src}")
            self.thread.start()
            actual_w = int(self.cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            actual_h = int(self.cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            print(f"[✓] Kamera {self.src} aktiv: {actual_w}x{actual_h} Pixel")
        else:
            self.running = False
            self.ret = False
            self.frame = None

    def _capture_loop(self):
        while self.running:
            if self.cap and self.cap.isOpened():
                ret, frame = self.cap.read()
                if ret and frame is not None:
                    with self.lock:
                        self.frame = frame
                        self.ret = ret
                else:
                    time.sleep(0.02)
            else:
                break

    def read(self):
        with self.lock:
            if self.frame is not None:
                return self.ret, self.frame.copy()
            return False, None

    def release(self):
        self.running = False
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=0.3)
        if self.cap:
            self.cap.release()
            self.cap = None


class LiveRadarApp:
    def __init__(self, preferred_cam: int = 1):
        self.plates_by_id = {}
        self.plates_by_alias = {}
        self.seen_map = {}
        self.trip_log = []
        self.cooldown_map = {}
        self.backup_name = "Kein Backup"

        # Scan-Konfiguration
        self.scan_interval = 0.0       # Standard: 0.0s = ECHTZEIT-DAUER-SCAN (Blitzer-Modus ~12 Scans/Sekunde)
        self.last_scan_time = 0.0
        self.active_detections = []    # Letzte erkannte Bounding-Boxen
        self.scan_in_progress = False
        self.night_boost = False       # Kontrastverstärker für Nachtfahrten
        self.scan_count = 0

        # Zoom-Stufen (1x, 2x, 3x, 5x Tele-Fokus)
        self.zoom_levels = [1.0, 2.0, 3.0, 5.0]
        self.zoom_idx = 0

        self.load_master_data()
        self.auto_load_backup()

        print("[+] Initialisiere KI-Erkennungsmodul...")
        self.detector = FastLicensePlateDetector.get_instance()

        # Kamera initialisieren: Zuerst bevorzugte Kamera probieren (Standard: 1 = Smartphone)
        self.cam_index = preferred_cam
        self.cam = ThreadedCamera(src=self.cam_index, width=1920, height=1080)
        if not self.cam.ret:
            print(f"[!] Kamera {self.cam_index} nicht verfügbar. Wechsle zu Kamera 0...")
            self.cam_index = 0
            self.cam = ThreadedCamera(src=self.cam_index, width=1920, height=1080)

    def load_master_data(self):
        if not DATA_DB_PATH.is_file():
            raise FileNotFoundError(f"data.db nicht gefunden: {DATA_DB_PATH}")

        conn = sqlite3.connect(DATA_DB_PATH)
        cur = conn.cursor()
        cur.execute("""
            SELECT id, kennzeichen, ort, ableitung, bundesland, anzahl, alt, besonderes, punkte
            FROM deutschland
            ORDER BY id
        """)
        rows = cur.fetchall()
        conn.close()

        for r in rows:
            pid, kz, ort, ableitung, bl, anzahl, alt, bes, pts = r
            if pid not in self.plates_by_id:
                self.plates_by_id[pid] = {
                    "id": pid,
                    "kuerzel_list": [],
                    "orte": [],
                    "ableitung": ableitung or "",
                    "bundesland": bl or "Bundesweit",
                    "punkte": pts if pts is not None else 10
                }
            p = self.plates_by_id[pid]
            if kz and kz.strip() and kz.strip() not in p["kuerzel_list"]:
                p["kuerzel_list"].append(kz.strip())
            if ort and ort.strip() and ort.strip() not in p["orte"]:
                p["orte"].append(ort.strip())

        for pid, p in self.plates_by_id.items():
            k_list = p["kuerzel_list"]
            kuerzel = " / ".join(k_list)
            stadt_kreis = " / ".join(p["orte"])
            plate_obj = {
                "id": pid,
                "kuerzel": kuerzel,
                "aliases": k_list,
                "stadt_kreis": stadt_kreis,
                "bundesland": p["bundesland"],
                "punkte": p["punkte"]
            }
            self.plates_by_id[pid] = plate_obj
            for alias in k_list:
                norm = alias.upper().replace("-", "").replace(" ", "")
                self.plates_by_alias[norm] = plate_obj
                self.plates_by_alias[alias.upper()] = plate_obj

        print(f"[+] {len(self.plates_by_id)} deutsche Kennzeichen geladen.")

    def auto_load_backup(self):
        candidates = []
        if DOWNLOADS_DIR.is_dir():
            for f in DOWNLOADS_DIR.glob("*KennzeichensammlerBackup*"):
                if f.is_file():
                    candidates.append(f)
            clean_db = DOWNLOADS_DIR / "kennzeichen_clean.db"
            if clean_db.is_file():
                candidates.append(clean_db)

        if not candidates:
            return

        candidates.sort(key=lambda x: x.stat().st_mtime, reverse=True)
        latest = candidates[0]
        self.backup_name = latest.name

        with open(latest, "rb") as f:
            data = f.read()

        if data.startswith(b"\x78\x9c") or data.startswith(b"\x78\x01") or data.startswith(b"\x78\xda"):
            try:
                data = zlib.decompress(data)
            except Exception:
                pass

        if data.startswith(b"KX1SQLite format 3"):
            data = data[3:]
        elif b"SQLite format 3" in data:
            idx = data.find(b"SQLite format 3")
            data = data[idx:]

        with tempfile.NamedTemporaryFile(delete=False) as tf:
            tf.write(data)
            tf_name = tf.name

        try:
            conn = sqlite3.connect(tf_name)
            rows = conn.cursor().execute("SELECT kennid, datum, land FROM gesehen").fetchall()
            conn.close()
        finally:
            try:
                os.unlink(tf_name)
            except OSError:
                pass

        for kid, datum, land in rows:
            if (land or "").lower() == "deutschland":
                self.seen_map[kid] = datum

        print(f"[+] Backup '{latest.name}' geladen: {len(self.seen_map)} / 1.069 bereits gesammelt.")

    def play_chime(self):
        try:
            winsound.Beep(523, 70)   # C5
            winsound.Beep(659, 70)   # E5
            winsound.Beep(784, 70)   # G5
            winsound.Beep(1046, 140) # C6
        except Exception:
            pass

    def save_capture(self, frame: np.ndarray, plate: dict, box_px: list, ocr_conf: float):
        annotated = frame.copy()
        x1, y1, x2, y2 = box_px
        h, w = frame.shape[:2]

        cv2.rectangle(annotated, (x1, y1), (x2, y2), (46, 204, 113), 3)

        label = f"[{plate['kuerzel']}] {ocr_conf:.0f}% sicher • {plate['stadt_kreis']} • NEU (+{plate['punkte']} Pkt.)"
        (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.7, 2)
        banner_y1 = max(0, y1 - th - 14)
        cv2.rectangle(annotated, (x1, banner_y1), (x1 + tw + 18, y1), (46, 204, 113), -1)
        cv2.putText(annotated, label, (x1 + 8, y1 - 8), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 0), 2, cv2.LINE_AA)

        now_str = datetime.now().strftime("%d.%m.%Y %H:%M:%S")
        cv2.putText(annotated, now_str, (w - 280, h - 20), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2, cv2.LINE_AA)

        timestamp = datetime.now().strftime('%Y-%m-%d_%H-%M-%S')
        safe_kz = plate['kuerzel'].replace(" ", "_").replace("/", "-")
        filename = f"{timestamp}_{safe_kz}.jpg"
        out_path = CAPTURES_DIR / filename
        cv2.imwrite(str(out_path), annotated, [cv2.IMWRITE_JPEG_QUALITY, 92])
        print(f"\n[📸 NEUER FUND] [{plate['kuerzel']}] ({ocr_conf:.0f}% sicher) • {plate['stadt_kreis']} (+{plate['punkte']} Pkt.) -> Gespeichert in captures/{filename}")
        return filename

    def _async_scan_frame(self, frame: np.ndarray):
        """Führt den hochauflösenden Scan im Hintergrund aus, ohne den Sucher zu blockieren."""
        try:
            self.scan_count += 1
            # Höhere Auflösung (imgsz=640) mit sofortiger OCR-Erkennung (sync_ocr=True)
            detections = self.detector.detect(frame, conf_thresh=0.20, imgsz=640, alias_map=self.plates_by_alias, sync_ocr=True)
            
            now = time.time()
            for det in detections:
                plate = det.get("plate")
                ocr_conf = det.get("ocr_confidence", 0.0)
                
                # Blitzer-Qualitätsfilter: Bei validem Ortskürzel ab 40% Text-Sicherheit werten
                if plate and ocr_conf >= 40.0:
                    pid = plate["id"]
                    is_missing = pid not in self.seen_map

                    if is_missing:
                        last_seen_time = self.cooldown_map.get(plate['kuerzel'], 0)
                        if now - last_seen_time > 10.0:
                            self.cooldown_map[plate['kuerzel']] = now
                            saved_file = self.save_capture(frame, plate, det["box_px"], ocr_conf)
                            self.trip_log.append({
                                "kuerzel": plate['kuerzel'],
                                "stadt_kreis": plate['stadt_kreis'],
                                "bundesland": plate['bundesland'],
                                "punkte": plate['punkte'],
                                "sicherheit": f"{ocr_conf:.0f}%",
                                "time": datetime.now().strftime("%H:%M:%S"),
                                "image": saved_file
                            })
                            self.last_hit_banner = f"NEU: [{plate['kuerzel']}] ({ocr_conf:.0f}% sicher) • {plate['stadt_kreis']} (+{plate['punkte']} Pkt.)"
                            self.banner_expire = now + 4.5
                            threading.Thread(target=self.play_chime, daemon=True).start()

            self.active_detections = detections
        finally:
            self.scan_in_progress = False

    def apply_zoom(self, frame: np.ndarray) -> np.ndarray:
        """Vergrößert den zentralen Straßenbereich (1x, 2x, 3x, 5x Tele-Ausschnitt)."""
        current_zoom = self.zoom_levels[self.zoom_idx]
        if current_zoom <= 1.0:
            return frame
        h, w = frame.shape[:2]
        crop_w = int(w / current_zoom)
        crop_h = int(h / current_zoom)
        x1 = (w - crop_w) // 2
        y1 = (h - crop_h) // 2
        cropped = frame[y1:y1 + crop_h, x1:x1 + crop_w]
        return cv2.resize(cropped, (w, h), interpolation=cv2.INTER_LINEAR)

    def trigger_scan(self, frame: np.ndarray):
        if not self.scan_in_progress and frame is not None:
            self.scan_in_progress = True
            self.last_scan_time = time.time()
            threading.Thread(target=self._async_scan_frame, args=(frame.copy(),), daemon=True).start()

    def run(self):
        window_name = "🚗 Kennzeichen Radar • HD-Sucher (60 FPS & Intervall-Scan)"
        try:
            cv2.namedWindow(window_name, cv2.WINDOW_NORMAL)
            cv2.resizeWindow(window_name, 1280, 720)
        except Exception as e:
            print(f"[!] Fenster-Warnung: {e}")

        fps_times = []
        self.last_hit_banner = None
        self.banner_expire = 0.0

        print("\n" + "=" * 65)
        print("   🚗  KENNZEICHEN RADAR • PRÄZISIONS-SCANNER AKTIV  🚗")
        print("   [Leertaste]  : Sofortiger Zwangsscan auf aktuellen Frame")
        print("   [z]          : Zoom umschalten (1x ➔ 2x ➔ 3x ➔ 5x)")
        print("   [1, 2, 3, 5] : Direkt-Zoom (1x, 2x, 3x, 5x)")
        print("   [c]          : Kamera wechseln (0 = Laptop, 1 = Handy)")
        print("   [+] / [-]    : Scan-Takt ändern (z.B. alle 0.5s / 1.0s / 2.0s)")
        print("   [e]          : Nacht-Boost / Kontrastfilter an/aus")
        print("   [s]          : Manueller Schnappschuss")
        print("   [q] / [ESC]  : Beenden & Reiseliste (CSV) exportieren")
        print("=" * 65 + "\n")

        while True:
            t0 = time.time()
            ret, frame = self.cam.read()
            if not ret or frame is None:
                time.sleep(0.01)
                continue

            # Digitaler Tele-Zoom (vergrößert entfernte Autos im Zentrum)
            frame = self.apply_zoom(frame)

            h, w = frame.shape[:2]
            now = time.time()

            # Nacht-Boost (CLAHE) bei Bedarf anwenden
            if self.night_boost:
                lab = cv2.cvtColor(frame, cv2.COLOR_BGR2LAB)
                l, a, b_chan = cv2.split(lab)
                clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))
                l = clahe.apply(l)
                enhanced = cv2.merge((l, a, b_chan))
                frame = cv2.cvtColor(enhanced, cv2.COLOR_LAB2BGR)

            # 1. Automatischer Intervall-Scan (z. B. alle 1,0 Sekunde)
            if now - self.last_scan_time >= self.scan_interval:
                self.trigger_scan(frame)

            # 2. Bounding-Boxen der letzten Erkennung zeichnen
            for det in self.active_detections:
                x1, y1, x2, y2 = det["box_px"]
                box_conf = det["confidence"]
                ocr_conf = det.get("ocr_confidence", 0.0)
                plate = det.get("plate")

                if plate and ocr_conf >= 40.0:
                    pid = plate["id"]
                    is_missing = pid not in self.seen_map
                    color = (46, 204, 113) if is_missing else (241, 196, 15)

                    cv2.rectangle(frame, (x1, y1), (x2, y2), color, 3)
                    status_text = "NEU!" if is_missing else "OK"
                    badge_label = f"[{plate['kuerzel']}] {ocr_conf:.0f}% sicher • {plate['stadt_kreis']} ({status_text})"
                    (tw, th), _ = cv2.getTextSize(badge_label, cv2.FONT_HERSHEY_SIMPLEX, 0.65, 2)
                    badge_y = max(0, y1 - th - 10)
                    cv2.rectangle(frame, (x1, badge_y), (x1 + tw + 14, y1), color, -1)
                    cv2.putText(frame, badge_label, (x1 + 6, y1 - 5), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (0, 0, 0), 2, cv2.LINE_AA)
                else:
                    # Bounding Box erkannt, Zeichen noch in Prüfung oder Schild weiter entfernt
                    cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 215, 255), 2)
                    scan_hint = f"Schild ({box_conf:.0f}%) • Scanne..."
                    cv2.putText(frame, scan_hint, (x1 + 4, max(15, y1 - 6)), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 215, 255), 2)

            # 3. FPS-Berechnung für flüssigen 60 FPS Sucher
            fps_times.append(time.time() - t0)
            if len(fps_times) > 20:
                fps_times.pop(0)
            avg_fps = len(fps_times) / sum(fps_times) if fps_times else 0

            # 4. Elegantes HUD Banner oben
            cv2.rectangle(frame, (0, 0), (w, 48), (15, 23, 42), -1)

            cam_name = "Handy (Cam 1)" if self.cam_index == 1 else f"Kamera {self.cam_index}"
            if self.scan_interval <= 0.05:
                scan_pulse = "⚡ ECHTZEIT-BLITZER (~12/s)"
            elif self.scan_in_progress:
                scan_pulse = "⚡ SCAN..."
            else:
                scan_pulse = f"Takt: {self.scan_interval:.1f}s"

            boost_str = " | Boost: AN" if self.night_boost else ""
            cur_zoom = self.zoom_levels[self.zoom_idx]
            zoom_str = f" | 🔍 {cur_zoom:.0f}x" if cur_zoom > 1.0 else " | 🔍 1x"

            hud_left = f"🚗 RADAR [{cam_name} {w}x{h}]{zoom_str} | {scan_pulse}{boost_str}"
            hud_right = f"Gesehen: {len(self.seen_map)}/1.069 | Trip: {len(self.trip_log)} | {avg_fps:.0f} FPS"

            cv2.putText(frame, hud_left, (16, 31), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (255, 255, 255), 2, cv2.LINE_AA)
            cv2.putText(frame, hud_right, (w - 440, 31), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (56, 189, 248), 2, cv2.LINE_AA)

            # 5. Neufund-Banner unten
            if self.last_hit_banner and now < self.banner_expire:
                cv2.rectangle(frame, (0, h - 55), (w, h), (46, 204, 113), -1)
                cv2.putText(frame, f"🎉 {self.last_hit_banner}", (20, h - 18), cv2.FONT_HERSHEY_SIMPLEX, 0.85, (0, 0, 0), 2, cv2.LINE_AA)

            cv2.imshow(window_name, frame)

            # Tastenabfrage
            key = cv2.waitKey(1) & 0xFF
            if key == ord('q') or key == 27:
                break
            elif key == ord(' '):  # Sofortiger Zwangsscan
                print("[*] Manueller Zwangsscan ausgelöst...")
                self.trigger_scan(frame)
            elif key == ord('z'):  # Zoom durchschalten
                self.zoom_idx = (self.zoom_idx + 1) % len(self.zoom_levels)
                print(f"[🔍] Zoom auf {self.zoom_levels[self.zoom_idx]:.0f}x gesetzt")
            elif key == ord('1'):
                self.zoom_idx = 0
                print("[🔍] Zoom: 1x (Normal)")
            elif key == ord('2'):
                self.zoom_idx = 1
                print("[🔍] Zoom: 2x")
            elif key == ord('3'):
                self.zoom_idx = 2
                print("[🔍] Zoom: 3x")
            elif key == ord('5'):
                self.zoom_idx = 3
                print("[🔍] Zoom: 5x (Tele-Fokus)")
            elif key == ord('c'):  # Kamera wechseln
                self.cam_index = 0 if self.cam_index == 1 else 1
                self.cam.open(self.cam_index)
            elif key == ord('+') or key == ord('='):
                self.scan_interval = min(5.0, round(self.scan_interval + 0.2, 1))
                print(f"[+] Scan-Intervall auf {self.scan_interval}s erhöht")
            elif key == ord('-'):
                self.scan_interval = max(0.0, round(self.scan_interval - 0.2, 1))
                if self.scan_interval <= 0.05:
                    print("[-] Scan-Modus: ⚡ ECHTZEIT-DAUER-SCAN (Maximale Geschwindigkeit)")
                else:
                    print(f"[-] Scan-Intervall auf {self.scan_interval}s verkürzt")
            elif key == ord('e'):
                self.night_boost = not self.night_boost
                print(f"[+] Nacht-Boost: {'Aktiviert' if self.night_boost else 'Deaktiviert'}")
            elif key == ord('s'):
                ts = datetime.now().strftime('%Y%m%d_%H%M%S')
                cv2.imwrite(str(CAPTURES_DIR / f"snapshot_{ts}.jpg"), frame)
                print(f"[+] Schnappschuss gespeichert: captures/snapshot_{ts}.jpg")

        self.cam.release()
        cv2.destroyAllWindows()

        if self.trip_log:
            csv_path = BASE_DIR / f"trip_{datetime.now().strftime('%Y%m%d_%H%M')}.csv"
            with open(csv_path, "w", encoding="utf-8-sig") as f:
                f.write("Kürzel;Ort / Landkreis;Bundesland;Punkte;Uhrzeit;Foto\n")
                for t in self.trip_log:
                    f.write(f"{t['kuerzel']};{t['stadt_kreis']};{t['bundesland']};{t['punkte']};{t['time']};{t.get('image') or ''}\n")
            print(f"\n[+] Trip mit {len(self.trip_log)} Funden erfolgreich exportiert nach: {csv_path.name}")

if __name__ == "__main__":
    preferred = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    app = LiveRadarApp(preferred_cam=preferred)
    app.run()
