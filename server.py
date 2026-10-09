#!/usr/bin/env python3
"""
Kennzeichen-Radar Backend Server mit KI-Kennzeichenerkennung (YOLOv8 + ALPR)
Vollständiges Tool: Backup-Upload bis zum automatischen Live-Erkennen auf der Straße.
"""

import os
import sys
import zlib
import json
import sqlite3
import tempfile
import urllib.parse
import io
import base64
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from typing import Dict, Any, List

# UTF-8 Konsole
sys.stdout.reconfigure(encoding='utf-8')

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "web"
CAPTURES_DIR = BASE_DIR / "captures"
CAPTURES_DIR.mkdir(parents=True, exist_ok=True)

DATA_DB_PATH = BASE_DIR / "data" / "data.db"
DOWNLOADS_DIR = Path.home() / "Downloads"

# Lazy-loaded Detector
_DETECTOR = None

def get_detector():
    global _DETECTOR
    if _DETECTOR is None:
        from detector import LicensePlateDetector
        _DETECTOR = LicensePlateDetector.get_instance()
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
    # 1. Zlib Decompression falls nötig
    if data.startswith(b"\x78\x9c") or data.startswith(b"\x78\x01") or data.startswith(b"\x78\xda"):
        try:
            data = zlib.decompress(data)
        except Exception as e:
            print(f"[!] Zlib Fehler: {e}")

    # 2. KX1 Header entfernen
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
        "trip_log": APP_STATE["trip_log"]
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

        # 1. Desktop Dashboard
        if path in ("/", "/index.html", "/laptop"):
            self.serve_file(STATIC_DIR / "index.html", "text/html; charset=utf-8")
        # 2. Smartphone Kamera HUD
        elif path in ("/cam", "/kamera", "/cam.html"):
            self.serve_file(STATIC_DIR / "cam.html", "text/html; charset=utf-8")
        # 3. Statische Assets
        elif path.startswith("/static/") or path.startswith("/web/"):
            rel_path = path.split("/", 2)[-1]
            file_path = STATIC_DIR / rel_path
            mime = self.get_mime_type(file_path)
            self.serve_file(file_path, mime)
        elif path.startswith("/css/") or path.startswith("/js/"):
            file_path = STATIC_DIR / path.lstrip("/")
            mime = self.get_mime_type(file_path)
            self.serve_file(file_path, mime)
        # 4. Gespeicherte Beweisfotos
        elif path.startswith("/captures/"):
            rel_path = path[len("/captures/"):]
            file_path = CAPTURES_DIR / rel_path
            self.serve_file(file_path, "image/jpeg")
        # 5. REST APIs
        elif path == "/api/data":
            self.send_json(get_full_data_payload())
        elif path == "/api/trip/export":
            self.export_trip_csv()
        else:
            self.send_error(404, "Nicht gefunden")

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        # API: KI-Kennzeichenerkennung (YOLOv8 + ALPR)
        if path == "/api/detect":
            content_length = int(self.headers.get("Content-Length", 0))
            payload = json.loads(self.rfile.read(content_length).decode('utf-8'))
            img_b64 = payload.get("image", "")

            if "," in img_b64:
                img_b64 = img_b64.split(",", 1)[1]

            try:
                img_bytes = base64.b64decode(img_b64)
            except Exception as e:
                self.send_json({"success": False, "error": f"Base64 Decode Fehler: {e}"}, status=400)
                return

            detector = get_detector()
            # Erkennung mit YOLO und deutschem Kennzeichen-Lookup
            detections = detector.detect(img_bytes, conf_thresh=0.20, imgsz=640, alias_map=APP_STATE["plates_by_alias"])

            # Status (missing / seen) ermitteln
            enriched_detections = []
            seen_map = APP_STATE["seen_map"]

            for det in detections:
                plate = det.get("plate")
                if plate:
                    is_seen = (plate["id"], "deutschland") in seen_map
                    status = "seen" if is_seen else "missing"
                    det["status"] = status
                    det["plate"]["status"] = status
                else:
                    det["status"] = "unknown"
                enriched_detections.append(det)

            self.send_json({
                "success": True,
                "count": len(enriched_detections),
                "detections": enriched_detections
            })

        # API: Foto-Capture mit Bounding-Box speichern & Trip erfassen
        elif path == "/api/capture":
            content_length = int(self.headers.get("Content-Length", 0))
            payload = json.loads(self.rfile.read(content_length).decode('utf-8'))
            query = payload.get("query", "").strip().upper()
            img_b64 = payload.get("image", "")
            box = payload.get("box", {})
            source = payload.get("source", "smartphone_cam")
            conf = payload.get("confidence", 85)

            if "," in img_b64:
                img_b64 = img_b64.split(",", 1)[1]

            try:
                img_bytes = base64.b64decode(img_b64)
            except Exception as e:
                img_bytes = None

            norm = query.replace("-", "").replace(" ", "")
            plate = APP_STATE["plates_by_alias"].get(norm) or APP_STATE["plates_by_alias"].get(query)

            if plate:
                pid = plate["id"]
                is_seen = (pid, "deutschland") in APP_STATE["seen_map"]
                status = "seen" if is_seen else "missing"

                # Foto mit passender Bounding-Box abspeichern
                saved_filename = None
                if img_bytes:
                    saved_filename = self.save_annotated_capture(img_bytes, plate, box, status, conf)

                time_str = datetime.now().strftime("%H:%M:%S")
                already_in_trip = any(item["id"] == pid for item in APP_STATE["trip_log"])
                
                # Nur neue Funde aufnehmen
                if not already_in_trip and status == "missing":
                    APP_STATE["trip_log"].append({
                        "id": pid,
                        "kuerzel": plate["kuerzel"],
                        "stadt_kreis": plate["stadt_kreis"],
                        "bundesland": plate["bundesland"],
                        "punkte": plate["punkte"],
                        "source": source,
                        "time": time_str,
                        "image": saved_filename,
                        "conf": conf
                    })

                self.send_json({
                    "success": True,
                    "plate": plate,
                    "status": status,
                    "saved_filename": saved_filename,
                    "already_in_trip": already_in_trip
                })
            else:
                self.send_json({"success": False, "error": f"Kennzeichen '{query}' nicht gefunden"}, status=404)

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

        # API: Manuell zum Trip hinzufügen
        elif path == "/api/trip/add":
            content_length = int(self.headers.get("Content-Length", 0))
            payload = json.loads(self.rfile.read(content_length).decode('utf-8'))
            pid = payload.get("id")
            source = payload.get("source", "manuell")
            time_str = payload.get("time") or datetime.now().strftime("%H:%M:%S")

            plate = APP_STATE["plates_by_id"].get(pid)
            if plate:
                if not any(item["id"] == pid for item in APP_STATE["trip_log"]):
                    APP_STATE["trip_log"].append({
                        "id": pid,
                        "kuerzel": plate["kuerzel"],
                        "stadt_kreis": plate["stadt_kreis"],
                        "bundesland": plate["bundesland"],
                        "punkte": plate["punkte"],
                        "source": source,
                        "time": time_str,
                        "image": None,
                        "conf": 100
                    })
                self.send_json({"success": True, "trip_log": APP_STATE["trip_log"]})
            else:
                self.send_json({"success": False, "error": "ID nicht gefunden"}, status=404)
        else:
            self.send_error(404, "API-Endpunkt nicht gefunden")

    def save_annotated_capture(self, img_bytes: bytes, plate: dict, box: dict, status: str, conf: float) -> str:
        """Speichert das Foto mit grünem Rahmen und Zeitstempel in captures/."""
        import cv2
        import numpy as np

        nparr = np.frombuffer(img_bytes, np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        if img is None:
            return None

        h, w = img.shape[:2]
        bx = max(0, int(box.get("x", 0.2) * w))
        by = max(0, int(box.get("y", 0.4) * h))
        bw = min(w - bx, int(box.get("w", 0.6) * w))
        bh = min(h - by, int(box.get("h", 0.2) * h))

        color = (46, 204, 113) if status == "missing" else (241, 196, 15) # BGR
        line_w = max(4, int(w * 0.005))

        cv2.rectangle(img, (bx, by), (bx + bw, by + bh), color, line_w)

        # Banner Beschriftung
        tag = "NEUER FUND!" if status == "missing" else "BEREITS GESEHEN"
        label = f"[{plate['kuerzel']}] {plate.get('stadt_kreis', '')} • {tag} ({conf:.0f}%)"

        font_scale = max(0.55, w / 1600.0)
        thickness = max(2, int(w * 0.002))
        (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, font_scale, thickness)

        banner_y1 = max(0, by - th - 14)
        banner_y2 = by
        cv2.rectangle(img, (bx, banner_y1), (bx + tw + 16, banner_y2), color, -1)
        cv2.putText(img, label, (bx + 8, by - 6), cv2.FONT_HERSHEY_SIMPLEX, font_scale, (0, 0, 0), thickness, cv2.LINE_AA)

        # Zeitstempel unten rechts
        now_str = datetime.now().strftime("%d.%m.%Y %H:%M:%S")
        cv2.putText(img, now_str, (w - 280, h - 20), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2, cv2.LINE_AA)

        safe_kz = plate['kuerzel'].replace(" ", "_").replace("/", "-")
        timestamp = datetime.now().strftime('%Y-%m-%d_%H-%M-%S')
        filename = f"{timestamp}_{safe_kz}.jpg"
        out_path = CAPTURES_DIR / filename
        cv2.imwrite(str(out_path), img, [cv2.IMWRITE_JPEG_QUALITY, 90])
        print(f"[+] Beweisfoto gesichert: {filename}")
        return filename

    def export_trip_csv(self):
        """Exportiert die während der Fahrt gesammelten Kennzeichen als CSV."""
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

    server = HTTPServer(("0.0.0.0", port), RadarServerHandler)
    print(f"\n=======================================================")
    print(f"🚀 KENNZEICHEN-RADAR SERVER AKTIV AUF PORT {port}")
    print(f"💻 Laptop-Dashboard: http://localhost:{port}/")
    print(f"📱 Smartphone-Kamera: http://<Deine-WLAN-IP>:{port}/cam")
    print(f"=======================================================\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[!] Server beendet.")
        server.server_close()

if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8080
    run(port)
