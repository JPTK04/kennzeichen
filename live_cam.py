#!/usr/bin/env python3
"""
Kennzeichen-Radar • Lokaler High-Speed Live-Kamera Scanner (USB / Webcam)
100% Offline-fähig – Kein WLAN, kein Hotspot nötig!
Direkte Kamera-Anbindung mit 30+ FPS & Echtzeit-KI auf der Laptop-CPU.
"""

import sys
import os
import time
import winsound
import sqlite3
import zlib
import tempfile
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

class LiveRadarApp:
    def __init__(self, cam_index: int = 0):
        self.cam_index = cam_index
        self.plates_by_id = {}
        self.plates_by_alias = {}
        self.seen_map = {}
        self.trip_log = []
        self.cooldown_map = {} # { 'ESB': timestamp }
        self.backup_name = "Kein Backup"

        self.load_master_data()
        self.auto_load_backup()

        print("[+] Initialisiere KI-Erkennungsmodul...")
        self.detector = FastLicensePlateDetector.get_instance()

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
        """Spielt einen erfreulichen Victory-Chime Ton bei Neufunden ab."""
        try:
            winsound.Beep(523, 70)   # C5
            winsound.Beep(659, 70)   # E5
            winsound.Beep(784, 70)   # G5
            winsound.Beep(1046, 140) # C6
        except Exception:
            pass

    def save_capture(self, frame: np.ndarray, plate: dict, box_px: list, conf: float):
        """Speichert das Beweisfoto mit grünem Rahmen ab."""
        annotated = frame.copy()
        x1, y1, x2, y2 = box_px
        h, w = frame.shape[:2]

        # Grüner Rahmen
        cv2.rectangle(annotated, (x1, y1), (x2, y2), (46, 204, 113), 3)

        # Banner oben
        label = f"[{plate['kuerzel']}] {plate['stadt_kreis']} • NEUER FUND (+{plate['punkte']} Pkt.)"
        (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.65, 2)
        banner_y1 = max(0, y1 - th - 12)
        cv2.rectangle(annotated, (x1, banner_y1), (x1 + tw + 16, y1), (46, 204, 113), -1)
        cv2.putText(annotated, label, (x1 + 8, y1 - 6), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (0, 0, 0), 2, cv2.LINE_AA)

        # Zeitstempel
        now_str = datetime.now().strftime("%d.%m.%Y %H:%M:%S")
        cv2.putText(annotated, now_str, (w - 260, h - 18), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (255, 255, 255), 2, cv2.LINE_AA)

        timestamp = datetime.now().strftime('%Y-%m-%d_%H-%M-%S')
        safe_kz = plate['kuerzel'].replace(" ", "_").replace("/", "-")
        filename = f"{timestamp}_{safe_kz}.jpg"
        out_path = CAPTURES_DIR / filename
        cv2.imwrite(str(out_path), annotated, [cv2.IMWRITE_JPEG_QUALITY, 90])
        print(f"[📸 NEUER FUND] {plate['kuerzel']} ({plate['stadt_kreis']}) -> Gespeichert als {filename}")
        return filename

    def run(self):
        print(f"\n[+] Öffne Kamera Index {self.cam_index}...")
        cap = cv2.VideoCapture(self.cam_index, cv2.CAP_DSHOW)
        
        # HD-Auflösung anfordern
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)

        if not cap.isOpened():
            print(f"[!] Konnte Kamera {self.cam_index} nicht öffnen! Versuche Standard-Backend...")
            cap = cv2.VideoCapture(self.cam_index)

        if not cap.isOpened():
            print(f"[!] Fehler: Keine Kamera unter Index {self.cam_index} gefunden.")
            return

        window_name = "🚗 Kennzeichen Radar • Live-Scanner (Offline)"
        cv2.namedWindow(window_name, cv2.WINDOW_NORMAL)
        cv2.resizeWindow(window_name, 1280, 720)

        fps_times = []
        last_found_banner = None
        banner_expire = 0

        print("\n" + "=" * 60)
        print("   🚗  LIVE-SCANNER AKTIV • TASTENBELEGUNG:  🚗")
        print("   [q] oder [ESC] : Beenden")
        print("   [c]            : Kamera wechseln (0, 1, 2...)")
        print("   [s]            : Schnappschuss speichern")
        print("=" * 60 + "\n")

        while True:
            t0 = time.time()
            ret, frame = cap.read()
            if not ret:
                time.sleep(0.05)
                continue

            h, w = frame.shape[:2]

            # 1. High-Speed YOLO Erkennung (Inferenz läuft in ~25-30ms)
            detections = self.detector.detect(frame, conf_thresh=0.22, imgsz=480, alias_map=self.plates_by_alias)

            now = time.time()

            # 2. Bounding-Boxes zeichnen
            for det in detections:
                x1, y1, x2, y2 = det["box_px"]
                conf = det["confidence"]
                plate = det.get("plate")
                code = det.get("matched_code")

                if plate:
                    pid = plate["id"]
                    is_seen = pid in self.seen_map
                    is_missing = not is_seen

                    # Farbe: Grün für Neufund, Blau für bereits gesammelt
                    color = (46, 204, 113) if is_missing else (241, 196, 15)

                    # Rahmen
                    cv2.rectangle(frame, (x1, y1), (x2, y2), color, 3)

                    # Pill-Badge
                    status_text = "NEU!" if is_missing else "OK"
                    badge_label = f"[{plate['kuerzel']}] {conf:.0f}% • {status_text}"
                    (tw, th), _ = cv2.getTextSize(badge_label, cv2.FONT_HERSHEY_SIMPLEX, 0.6, 2)
                    badge_y = max(0, y1 - th - 10)
                    cv2.rectangle(frame, (x1, badge_y), (x1 + tw + 12, y1), color, -1)
                    cv2.putText(frame, badge_label, (x1 + 6, y1 - 5), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 0), 2, cv2.LINE_AA)

                    # Neuer Fund erfassen (mit Cooldown-Schutz von 12 Sekunden pro Kennzeichen)
                    if is_missing:
                        last_seen_time = self.cooldown_map.get(plate['kuerzel'], 0)
                        if now - last_seen_time > 12.0:
                            self.cooldown_map[plate['kuerzel']] = now
                            saved_file = self.save_capture(frame, plate, [x1, y1, x2, y2], conf)
                            self.trip_log.append({
                                "kuerzel": plate['kuerzel'],
                                "stadt_kreis": plate['stadt_kreis'],
                                "bundesland": plate['bundesland'],
                                "punkte": plate['punkte'],
                                "time": datetime.now().strftime("%H:%M:%S"),
                                "image": saved_file
                            })
                            last_found_banner = f"NEUER FUND: [{plate['kuerzel']}] (+{plate['punkte']} Pkt.) • {plate['stadt_kreis']}"
                            banner_expire = now + 4.0
                            self.play_chime()

                else:
                    # Bounding Box ohne erkanntes deutsches Kürzel (Gelb)
                    cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 215, 255), 2)
                    cv2.putText(frame, f"KFZ {conf:.0f}%", (x1 + 4, y1 - 6), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 215, 255), 1)

            # 3. Top HUD Status-Balken
            fps_times.append(time.time() - t0)
            if len(fps_times) > 15:
                fps_times.pop(0)
            avg_fps = len(fps_times) / sum(fps_times) if fps_times else 0

            # Dunkles HUD-Banner oben
            cv2.rectangle(frame, (0, 0), (w, 45), (15, 23, 42), -1)
            hud_text = f"🚗 RADAR (Kamera {self.cam_index}) | Gesehen: {len(self.seen_map)}/1.069 | Fehlend: {1069 - len(self.seen_map)} | Trip: {len(self.trip_log)} | FPS: {avg_fps:.1f}"
            cv2.putText(frame, hud_text, (15, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (255, 255, 255), 2, cv2.LINE_AA)

            # 4. Banner bei neuem Treffer
            if last_found_banner and now < banner_expire:
                cv2.rectangle(frame, (0, h - 55), (w, h), (46, 204, 113), -1)
                cv2.putText(frame, f"🎉 {last_found_banner}", (20, h - 18), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 0), 2, cv2.LINE_AA)

            cv2.imshow(window_name, frame)

            # Tastenabfrage
            key = cv2.waitKey(1) & 0xFF
            if key == ord('q') or key == 27: # q oder ESC
                break
            elif key == ord('c'): # Kamera wechseln
                cap.release()
                self.cam_index = (self.cam_index + 1) % 4
                print(f"[+] Wechsle zu Kamera Index {self.cam_index}...")
                cap = cv2.VideoCapture(self.cam_index, cv2.CAP_DSHOW)
                if not cap.isOpened():
                    cap = cv2.VideoCapture(self.cam_index)
            elif key == ord('s'): # Schnappschuss
                ts = datetime.now().strftime('%Y%m%d_%H%M%S')
                cv2.imwrite(str(CAPTURES_DIR / f"snapshot_{ts}.jpg"), frame)
                print(f"[+] Manueller Schnappschuss gesichert: snapshot_{ts}.jpg")

        cap.release()
        cv2.destroyAllWindows()

        # Trip Log beim Beenden als CSV exportieren
        if self.trip_log:
            csv_path = BASE_DIR / f"trip_{datetime.now().strftime('%Y%m%d_%H%M')}.csv"
            with open(csv_path, "w", encoding="utf-8-sig") as f:
                f.write("Kürzel;Ort / Landkreis;Bundesland;Punkte;Uhrzeit;Foto\n")
                for t in self.trip_log:
                    f.write(f"{t['kuerzel']};{t['stadt_kreis']};{t['bundesland']};{t['punkte']};{t['time']};{t.get('image') or ''}\n")
            print(f"\n[+] Trip erfolgreich exportiert nach: {csv_path.name}")

if __name__ == "__main__":
    cam_id = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    app = LiveRadarApp(cam_index=cam_id)
    app.run()
