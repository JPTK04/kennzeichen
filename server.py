#!/usr/bin/env python3
"""
Kennzeichen-Radar • Minimalistisches All-in-One Cockpit & REST-Server
Integrierter High-Speed Live-Kamerastream (MJPEG, 30+ FPS), KI-Kennzeichenerkennung (YOLOv8)
und automatische Fund-Verwaltung direkt im Browser ohne Scrollen.
"""

import os
import sys
import zlib
import json
import time
import sqlite3
import tempfile
import urllib.parse
import threading
import io
import base64
from datetime import datetime
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from typing import Dict, Any, List

import cv2
import numpy as np

# UTF-8 Konsole
sys.stdout.reconfigure(encoding='utf-8')

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "web"
CAPTURES_DIR = BASE_DIR / "captures"
CAPTURES_DIR.mkdir(parents=True, exist_ok=True)

DATA_DB_PATH = BASE_DIR / "data" / "data.db"
DOWNLOADS_DIR = Path.home() / "Downloads"

from detector import FastLicensePlateDetector

# Lazy-loaded Detector
_DETECTOR = None

def get_detector():
    global _DETECTOR
    if _DETECTOR is None:
        _DETECTOR = FastLicensePlateDetector.get_instance()
    return _DETECTOR

# Globaler Anwendungsstatus
APP_STATE = {
    "plates": [],           # Alle 1.069 deutschen Kennzeichen
    "plates_by_id": {},
    "plates_by_alias": {},
    "seen_map": {},         # { (kennid, land): datum }
    "trip_log": [],         # [ { "id", "kuerzel", "stadt_kreis", "bundesland", "punkte", "time", "source", "image", "conf" } ]
    "backup_info": {
        "filename": "Kein Backup geladen",
        "seen_count": 0,
        "de_seen_count": 0,
        "date": "-"
    }
}

class CameraStreamer:
    """Verwaltet den hardware-beschleunigten Kamera-Videostream (USB / Webcam)."""
    def __init__(self):
        self.cam_index = 0
        self.cap = None
        self.running = True
        self.paused = False
        self.lock = threading.Lock()
        self.latest_jpeg = None
        self.frame_id = 0
        self.last_hit = None
        self.cooldown_map = {}
        self.fps = 0.0
        self.thread = None
        self.sound_enabled = True

    def start(self):
        if self.thread is None or not self.thread.is_alive():
            self.running = True
            self.thread = threading.Thread(target=self._capture_loop, daemon=True, name="CamCapture")
            self.thread.start()

    def set_camera(self, index: int):
        with self.lock:
            self.cam_index = index
            if self.cap:
                self.cap.release()
                self.cap = None

    def toggle_pause(self):
        self.paused = not self.paused
        return self.paused

    def toggle_sound(self):
        self.sound_enabled = not self.sound_enabled
        return self.sound_enabled

    def _play_sound(self):
        if not self.sound_enabled:
            return
        try:
            import winsound
            winsound.Beep(523, 60)
            winsound.Beep(659, 60)
            winsound.Beep(784, 60)
            winsound.Beep(1046, 120)
        except Exception:
            pass

    def get_jpeg(self) -> bytes:
        with self.lock:
            return self.latest_jpeg

    def _capture_loop(self):
        detector = get_detector()
        fps_history = []

        while self.running:
            if self.paused:
                time.sleep(0.08)
                continue

            if self.cap is None or not self.cap.isOpened():
                # DirectShow für Windows
                self.cap = cv2.VideoCapture(self.cam_index, cv2.CAP_DSHOW)
                if not self.cap.isOpened():
                    self.cap = cv2.VideoCapture(self.cam_index)

                if not self.cap.isOpened():
                    placeholder = np.zeros((480, 854, 3), dtype=np.uint8)
                    cv2.putText(placeholder, f"Suche Kamera #{self.cam_index}...", (60, 240), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2)
                    _, buf = cv2.imencode('.jpg', placeholder, [cv2.IMWRITE_JPEG_QUALITY, 70])
                    with self.lock:
                        self.latest_jpeg = buf.tobytes()
                    time.sleep(1.0)
                    continue

                self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
                self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)

            t0 = time.time()
            ret, frame = self.cap.read()
            if not ret or frame is None:
                time.sleep(0.04)
                continue

            h, w = frame.shape[:2]
            detections = detector.detect(frame, conf_thresh=0.22, imgsz=480, alias_map=APP_STATE["plates_by_alias"])
            now = time.time()

            for det in detections:
                x1, y1, x2, y2 = det["box_px"]
                conf = det["confidence"]
                plate = det.get("plate")

                if plate:
                    pid = plate["id"]
                    is_seen = (pid, "deutschland") in APP_STATE["seen_map"]
                    is_missing = not is_seen

                    color = (46, 204, 113) if is_missing else (241, 196, 15)
                    cv2.rectangle(frame, (x1, y1), (x2, y2), color, 3)

                    status_text = "NEU!" if is_missing else "OK"
                    label = f"[{plate['kuerzel']}] {conf:.0f}% • {status_text}"
                    (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.6, 2)
                    badge_y = max(0, y1 - th - 10)
                    cv2.rectangle(frame, (x1, badge_y), (x1 + tw + 12, y1), color, -1)
                    cv2.putText(frame, label, (x1 + 6, y1 - 5), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 0), 2, cv2.LINE_AA)

                    self.last_hit = {
                        "id": pid,
                        "kuerzel": plate["kuerzel"],
                        "stadt_kreis": plate["stadt_kreis"],
                        "bundesland": plate["bundesland"],
                        "punkte": plate["punkte"],
                        "status": "missing" if is_missing else "seen",
                        "conf": conf,
                        "time": datetime.now().strftime("%H:%M:%S")
                    }

                    if is_missing:
                        last_t = self.cooldown_map.get(plate["kuerzel"], 0)
                        if now - last_t > 12.0:
                            self.cooldown_map[plate["kuerzel"]] = now
                            safe_kz = plate['kuerzel'].replace(" ", "_").replace("/", "-")
                            ts = datetime.now().strftime('%Y-%m-%d_%H-%M-%S')
                            filename = f"{ts}_{safe_kz}.jpg"
                            out_path = CAPTURES_DIR / filename
                            cv2.imwrite(str(out_path), frame, [cv2.IMWRITE_JPEG_QUALITY, 90])

                            APP_STATE["trip_log"].append({
                                "id": pid,
                                "kuerzel": plate["kuerzel"],
                                "stadt_kreis": plate["stadt_kreis"],
                                "bundesland": plate["bundesland"],
                                "punkte": plate["punkte"],
                                "source": "usb_cam",
                                "time": datetime.now().strftime("%H:%M:%S"),
                                "image": filename,
                                "conf": conf
                            })
                            threading.Thread(target=self._play_sound, daemon=True).start()
                else:
                    cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 215, 255), 2)
                    cv2.putText(frame, f"KFZ {conf:.0f}%", (x1 + 4, y1 - 6), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 215, 255), 1)

            dt = time.time() - t0
            fps_history.append(dt)
            if len(fps_history) > 15:
                fps_history.pop(0)
            self.fps = round(len(fps_history) / sum(fps_history), 1) if fps_history else 0

            # JPEG Komprimierung (Qualität 78: extrem schnell, gestochen scharf)
            _, jpeg_buf = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 78])
            with self.lock:
                self.frame_id += 1
                self.latest_jpeg = jpeg_buf.tobytes()

            if dt < 0.033:
                time.sleep(0.033 - dt)

CAM_STREAMER = CameraStreamer()

def load_master_data():
    """Lädt alle deutschen Kennzeichen aus data/data.db."""
    if not DATA_DB_PATH.is_file():
        raise FileNotFoundError(f"Stammdaten nicht gefunden unter: {DATA_DB_PATH}")

    conn = sqlite3.connect(DATA_DB_PATH)
    cur = conn.cursor()
    cur.execute("""
        SELECT id, kennzeichen, ort, ableitung, bundesland, anzahl, alt, besonderes, punkte
        FROM deutschland
        ORDER BY id
    """)
    rows = cur.fetchall()
    conn.close()

    plates_by_id = {}
    for r in rows:
        pid, kz, ort, ableitung, bl, anzahl, alt, bes, pts = r
        if pid not in plates_by_id:
            plates_by_id[pid] = {
                "id": pid,
                "kuerzel_list": [],
                "orte": [],
                "ableitung": ableitung or "",
                "bundesland": bl or ("Diplomatenkennzeichen" if bes == "Diplomatenkennzeichen" else "Bundesweit"),
                "alt": alt or "",
                "besonderes": bes or "",
                "punkte": pts if pts is not None else 10
            }
        p = plates_by_id[pid]
        if kz and kz.strip() and kz.strip() not in p["kuerzel_list"]:
            p["kuerzel_list"].append(kz.strip())
        if ort and ort.strip() and ort.strip() not in p["orte"]:
            p["orte"].append(ort.strip())

    formatted_plates = []
    plates_by_alias = {}

    for pid, p in plates_by_id.items():
        k_list = p["kuerzel_list"]
        if len(k_list) == 1:
            kuerzel = k_list[0]
        else:
            hyphenated = [k for k in k_list if '-' in k]
            kuerzel = " / ".join(hyphenated) if hyphenated else " / ".join(k_list)

        stadt_kreis = " / ".join(p["orte"])
        plate_obj = {
            "id": pid,
            "kuerzel": kuerzel,
            "aliases": k_list,
            "stadt_kreis": stadt_kreis,
            "ableitung": p["ableitung"],
            "bundesland": p["bundesland"],
            "alt": p["alt"],
            "besonderes": p["besonderes"],
            "punkte": p["punkte"]
        }
        formatted_plates.append(plate_obj)

        for alias in k_list:
            norm = alias.upper().replace("-", "").replace(" ", "")
            plates_by_alias[norm] = plate_obj
            plates_by_alias[alias.upper()] = plate_obj

    APP_STATE["plates"] = formatted_plates
    APP_STATE["plates_by_id"] = {p["id"]: p for p in formatted_plates}
    APP_STATE["plates_by_alias"] = plates_by_alias
    print(f"[+] Stammdaten geladen: {len(formatted_plates)} deutsche Kennzeichen hinterlegt.")

def parse_backup_bytes(data: bytes, filename: str = "Unbekannt"):
    """Dekomprimiert und parst ein Android-App-Backup (.db oder binär zlib/KX1)."""
    if data.startswith(b"\x78\x9c") or data.startswith(b"\x78\x01") or data.startswith(b"\x78\xda"):
        try:
            data = zlib.decompress(data)
        except Exception as e:
            print(f"[!] Zlib Fehler: {e}")

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
        cur = conn.cursor()
        rows = cur.execute("SELECT kennid, datum, land FROM gesehen").fetchall()
        conn.close()
    finally:
        try:
            os.unlink(tf_name)
        except OSError:
            pass

    seen_map = {}
    de_count = 0
    latest_date = "-"
    for kid, datum, land in rows:
        land_lower = (land or "").lower()
        seen_map[(kid, land_lower)] = datum
        if land_lower == "deutschland":
            de_count += 1
            latest_date = datum

    APP_STATE["seen_map"] = seen_map
    APP_STATE["backup_info"] = {
        "filename": filename,
        "seen_count": len(rows),
        "de_seen_count": de_count,
        "date": latest_date
    }
    print(f"[+] Backup geladen ({filename}): {len(rows)} Gesamt, {de_count} Deutschland.")

def auto_load_latest_backup():
    """Findet automatisch das neueste Backup im Downloads-Ordner."""
    candidates = []
    if DOWNLOADS_DIR.is_dir():
        for f in DOWNLOADS_DIR.glob("*KennzeichensammlerBackup*"):
            if f.is_file():
                candidates.append(f)
        clean_db = DOWNLOADS_DIR / "kennzeichen_clean.db"
        if clean_db.is_file():
            candidates.append(clean_db)

    if candidates:
        candidates.sort(key=lambda x: x.stat().st_mtime, reverse=True)
        latest = candidates[0]
        print(f"[+] Lade automatisches Backup aus Downloads: {latest.name}")
        with open(latest, "rb") as f:
            parse_backup_bytes(f.read(), latest.name)

def get_full_data_payload() -> Dict[str, Any]:
    """Erstellt das JSON-Paket für das Web-Dashboard."""
    seen_map = APP_STATE["seen_map"]
    plates = []
    bl_stats = {}

    total_de = len(APP_STATE["plates"])
    seen_de = 0
    total_points_earned = 0
    total_points_possible = 0

    for p in APP_STATE["plates"]:
        is_seen = (p["id"], "deutschland") in seen_map
        seen_date = seen_map.get((p["id"], "deutschland"), "")
        if is_seen:
            seen_de += 1
            total_points_earned += p["punkte"]
        total_points_possible += p["punkte"]

        plate_data = dict(p)
        plate_data["status"] = "seen" if is_seen else "missing"
        plate_data["seen_date"] = seen_date
        plates.append(plate_data)

        bl = p["bundesland"]
        if bl not in bl_stats:
            bl_stats[bl] = {"total": 0, "seen": 0, "missing": 0}
        bl_stats[bl]["total"] += 1
        if is_seen:
            bl_stats[bl]["seen"] += 1
        else:
            bl_stats[bl]["missing"] += 1

    missing_de = total_de - seen_de
    percent_seen = round((seen_de / total_de * 100), 1) if total_de > 0 else 0

    return {
        "summary": {
            "total_de": total_de,
            "seen_de": seen_de,
            "missing_de": missing_de,
            "percent_seen": percent_seen,
            "points_earned": total_points_earned,
            "points_possible": total_points_possible,
            "backup_info": APP_STATE["backup_info"]
        },
        "plates": plates,
        "bundeslaender": bl_stats,
        "trip_log": APP_STATE["trip_log"],
        "camera_status": {
            "fps": CAM_STREAMER.fps,
            "cam_index": CAM_STREAMER.cam_index,
            "paused": CAM_STREAMER.paused,
            "sound": CAM_STREAMER.sound_enabled,
            "last_hit": CAM_STREAMER.last_hit
        }
    }

class RadarServerHandler(BaseHTTPRequestHandler):
    def end_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Filename")
        super().end_headers()

    def do_OPTIONS(self):
        self.send_response(200)
        self.end_headers()

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        # 1. Desktop Minimal-Cockpit
        if path in ("/", "/index.html", "/cockpit"):
            self.serve_file(STATIC_DIR / "index.html", "text/html; charset=utf-8")

        # 2. Native MJPEG Live Video Stream (30+ FPS direkt im Browser)
        elif path == "/video_feed":
            self.send_response(200)
            self.send_header("Age", "0")
            self.send_header("Cache-Control", "no-cache, private")
            self.send_header("Pragma", "no-cache")
            self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=FRAME")
            self.end_headers()

            last_id = -1
            try:
                while True:
                    with CAM_STREAMER.lock:
                        cur_id = CAM_STREAMER.frame_id
                        frame_bytes = CAM_STREAMER.latest_jpeg
                    if cur_id != last_id and frame_bytes:
                        last_id = cur_id
                        self.wfile.write(b"--FRAME\r\n")
                        self.send_header("Content-Type", "image/jpeg")
                        self.send_header("Content-Length", str(len(frame_bytes)))
                        self.end_headers()
                        self.wfile.write(frame_bytes)
                        self.wfile.write(b"\r\n")
                    time.sleep(0.015)
            except (ConnectionResetError, BrokenPipeError):
                pass
            return

        # 3. Smartphone Fallback View
        elif path in ("/cam", "/kamera", "/cam.html"):
            self.serve_file(STATIC_DIR / "cam.html", "text/html; charset=utf-8")

        # 4. Statische Assets & Stylesheets
        elif path in ("/style.css", "/css/style.css"):
            self.serve_file(STATIC_DIR / "style.css", "text/css; charset=utf-8")
        elif path in ("/app.js", "/js/app.js"):
            self.serve_file(STATIC_DIR / "js" / "app.js", "application/javascript; charset=utf-8")
        elif path.startswith("/static/") or path.startswith("/web/"):
            rel_path = path.split("/", 2)[-1]
            file_path = STATIC_DIR / rel_path
            mime = self.get_mime_type(file_path)
            self.serve_file(file_path, mime)
        elif path.startswith("/css/") or path.startswith("/js/"):
            file_path = STATIC_DIR / path.lstrip("/")
            mime = self.get_mime_type(file_path)
            self.serve_file(file_path, mime)

        # 5. Gespeicherte Beweisfotos
        elif path.startswith("/captures/"):
            rel_path = path[len("/captures/"):]
            file_path = CAPTURES_DIR / rel_path
            self.serve_file(file_path, "image/jpeg")

        # 6. REST APIs
        elif path == "/api/data":
            self.send_json(get_full_data_payload())
        elif path == "/api/status":
            self.send_json({
                "fps": CAM_STREAMER.fps,
                "cam_index": CAM_STREAMER.cam_index,
                "paused": CAM_STREAMER.paused,
                "sound": CAM_STREAMER.sound_enabled,
                "last_hit": CAM_STREAMER.last_hit,
                "trip_count": len(APP_STATE["trip_log"]),
                "summary": get_full_data_payload()["summary"]
            })
        elif path == "/api/trip/export":
            self.export_trip_csv()
        else:
            self.send_error(404, "Nicht gefunden")

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        # Kamera umschalten
        if path == "/api/camera/switch":
            content_length = int(self.headers.get("Content-Length", 0))
            payload = json.loads(self.rfile.read(content_length).decode('utf-8'))
            idx = int(payload.get("index", 0))
            CAM_STREAMER.set_camera(idx)
            self.send_json({"success": True, "cam_index": idx})

        # Pause / Weiter
        elif path == "/api/camera/toggle":
            paused = CAM_STREAMER.toggle_pause()
            self.send_json({"success": True, "paused": paused})

        # Sound An / Aus
        elif path == "/api/camera/sound":
            sound = CAM_STREAMER.toggle_sound()
            self.send_json({"success": True, "sound": sound})

        # API: Backup hochladen
        elif path == "/api/upload":
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length)
            filename = self.headers.get("X-Filename", "Hochgeladenes-Backup")
            filename = urllib.parse.unquote(filename)

            content_type = self.headers.get("Content-Type", "")
            if "multipart/form-data" in content_type:
                try:
                    boundary = content_type.split("boundary=")[1].encode()
                    parts = body.split(b"--" + boundary)
                    for part in parts:
                        if b"filename=" in part:
                            header, file_bytes = part.split(b"\r\n\r\n", 1)
                            if file_bytes.endswith(b"\r\n"):
                                file_bytes = file_bytes[:-2]
                            body = file_bytes
                            break
                except Exception as e:
                    print("[!] Multipart parsing error:", e)

            try:
                parse_backup_bytes(body, filename)
                self.send_json({"success": True, "data": get_full_data_payload()})
            except Exception as e:
                self.send_json({"success": False, "error": str(e)}, status=400)

        # API: Manuelle Eingabe zum Trip hinzufügen
        elif path == "/api/trip/add":
            content_length = int(self.headers.get("Content-Length", 0))
            payload = json.loads(self.rfile.read(content_length).decode('utf-8'))
            query = payload.get("query", "").strip().upper()
            norm = query.replace("-", "").replace(" ", "")

            plate = APP_STATE["plates_by_alias"].get(norm) or APP_STATE["plates_by_alias"].get(query)
            if plate:
                pid = plate["id"]
                is_seen = (pid, "deutschland") in APP_STATE["seen_map"]
                status = "seen" if is_seen else "missing"

                already_in_trip = any(item["id"] == pid for item in APP_STATE["trip_log"])
                if not already_in_trip and status == "missing":
                    APP_STATE["trip_log"].append({
                        "id": pid,
                        "kuerzel": plate["kuerzel"],
                        "stadt_kreis": plate["stadt_kreis"],
                        "bundesland": plate["bundesland"],
                        "punkte": plate["punkte"],
                        "source": "manuell",
                        "time": datetime.now().strftime("%H:%M:%S"),
                        "image": None,
                        "conf": 100
                    })
                self.send_json({"success": True, "plate": plate, "status": status})
            else:
                self.send_json({"success": False, "error": f"Kennzeichen '{query}' nicht gefunden"}, status=404)
        else:
            self.send_error(404, "API-Endpunkt nicht gefunden")

    def export_trip_csv(self):
        output = io.StringIO()
        output.write("Kürzel;Stadt/Landkreis;Bundesland;Punkte;Uhrzeit;Quelle;Beweisfoto\n")
        for item in APP_STATE["trip_log"]:
            output.write(f"{item['kuerzel']};{item['stadt_kreis']};{item['bundesland']};{item['punkte']};{item['time']};{item['source']};{item.get('image') or ''}\n")

        data = output.getvalue().encode('utf-8-sig')
        self.send_response(200)
        self.send_header("Content-Type", "text/csv; charset=utf-8")
        self.send_header("Content-Disposition", f"attachment; filename=trip_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def serve_file(self, file_path: Path, mime: str):
        if not file_path.is_file():
            self.send_error(404, "Datei nicht gefunden")
            return
        try:
            with open(file_path, "rb") as f:
                content = f.read()
            self.send_response(200)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)
        except Exception as e:
            self.send_error(500, str(e))

    def send_json(self, data: Any, status: int = 200):
        body = json.dumps(data, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def get_mime_type(self, path: Path) -> str:
        ext = path.suffix.lower()
        mimes = {
            ".html": "text/html; charset=utf-8",
            ".css": "text/css; charset=utf-8",
            ".js": "application/javascript; charset=utf-8",
            ".json": "application/json; charset=utf-8",
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".svg": "image/svg+xml",
            ".ico": "image/x-icon"
        }
        return mimes.get(ext, "application/octet-stream")

def run(port: int = 8080):
    load_master_data()
    auto_load_latest_backup()
    
    # Live-Kamerastream starten
    CAM_STREAMER.start()

    server = ThreadingHTTPServer(("0.0.0.0", port), RadarServerHandler)
    print(f"\n=======================================================")
    print(f"🚀 KENNZEICHEN-RADAR COCKPIT AKTIV AUF PORT {port}")
    print(f"💻 Browser-Cockpit (Kein Scrollen): http://localhost:{port}/")
    print(f"🎥 Live-Videostream (30+ FPS):       http://localhost:{port}/video_feed")
    print(f"=======================================================\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[!] Server beendet.")
        server.server_close()

if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8080
    run(port)
