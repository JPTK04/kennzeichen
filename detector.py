#!/usr/bin/env python3
"""
High-Speed KI-Kennzeichenerkennung (ALPR) mit YOLOv8 & Intelligenter FE-Schrift OCR
Optimiert für 30+ FPS Echtzeit-Tracking auf Intel Core i7 / Multi-Core CPUs.
Strikte Links-nach-Rechts-Zonierung für korrekte deutsche Ortskürzel (ohne Plaketten/Erkennungsnummern).
"""

import os
import sys
import re
import time
import io
import base64
import warnings
from pathlib import Path
from typing import List, Dict, Any, Tuple, Optional
from concurrent.futures import ThreadPoolExecutor

# Warnungen unterdrücken
warnings.filterwarnings("ignore", category=UserWarning)

import torch
# CPU-Threads auf 4 begrenzen, damit Video-Stream und Benutzeroberfläche flüssig bei 30+ FPS bleiben
torch.set_num_threads(4)

import cv2
import numpy as np

sys.stdout.reconfigure(encoding='utf-8')

BASE_DIR = Path(__file__).resolve().parent
MODEL_PATH = BASE_DIR / "models" / "license_plate_detector.pt"

class FastLicensePlateDetector:
    _instance = None

    def __init__(self, model_path: Optional[Path] = None):
        from ultralytics import YOLO
        import easyocr

        self.model_path = model_path or MODEL_PATH
        if not self.model_path.is_file():
            raise FileNotFoundError(f"YOLO-Modell nicht gefunden: {self.model_path}")

        print(f"[KI] Lade High-Speed YOLOv8 ({self.model_path.name})...")
        self.yolo = YOLO(str(self.model_path))

        print("[KI] Initialisiere asynchrone OCR-Engine...")
        self.ocr = easyocr.Reader(['de', 'en'], gpu=False, verbose=False)
        self.ocr_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="PlateOCR")

        # Cache für aktive Kennzeichen: { grid_key: { "code", "plate", "timestamp" } }
        self.tracked_cache = {}
        # Cooldown für fehlgeschlagene Erkennungen, um CPU-Überlastung zu verhindern: { grid_key: timestamp }
        self.failed_cache = {}
        self.pending_crops = set()
        print("[KI] High-Speed Pipeline mit 30+ FPS Tracking & intelligenter Zonen-OCR bereit!")

    @classmethod
    def get_instance(cls):
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def _prepare_image(self, img_input: Any) -> Tuple[np.ndarray, int, int]:
        if isinstance(img_input, (str, Path)):
            img = cv2.imread(str(img_input))
            if img is None:
                raise ValueError(f"Bild konnte nicht geladen werden: {img_input}")
        elif isinstance(img_input, bytes):
            nparr = np.frombuffer(img_input, np.uint8)
            img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
            if img is None:
                raise ValueError("Ungültige Bild-Bytes")
        elif isinstance(img_input, np.ndarray):
            img = img_input
        else:
            raise TypeError(f"Nicht unterstützter Bildtyp: {type(img_input)}")

        h, w = img.shape[:2]
        return img, w, h

    def _get_grid_key(self, x1: int, y1: int, x2: int, y2: int, w: int, h: int) -> str:
        """Erzeugt einen räumlichen Raster-Schlüssel zur Nachverfolgung über Frames hinweg."""
        cx = int(((x1 + x2) / 2) / (w / 10)) # 10x10 Raster
        cy = int(((y1 + y2) / 2) / (h / 10))
        return f"{cx}_{cy}"

    def _async_ocr_worker(self, crop: np.ndarray, grid_key: str, alias_map: Dict[str, Any]):
        """
        Spezialisierte Erkennung des deutschen Kennzeichen-Ortskürzels:
        - Strikte Sortierung von Links nach Rechts (Ortskürzel steht IMMER ganz links)
        - Blauen EU-Balken abtrennen (erste ~12-14%)
        - Mittige Plaketten & rechte Zufallsbuchstaben (Erkennungsnummer) ignorieren
        - Cooldown bei Fehlversuchen, damit FPS niemals einbricht
        """
        now = time.time()
        try:
            ch, cw = crop.shape[:2]
            if ch < 14 or cw < 28:
                self.failed_cache[grid_key] = now
                return

            # 1. Auf optimale OCR-Höhe (90px) skalieren
            target_h = 90
            scale = target_h / ch
            target_w = int(cw * scale)
            scaled = cv2.resize(crop, (target_w, target_h), interpolation=cv2.INTER_CUBIC)

            # 2. Textboxen mit EasyOCR finden (detail=1 für X-Koordinaten)
            results = self.ocr.readtext(scaled, detail=1)

            # Strikte Sortierung von Links nach Rechts
            sorted_boxes = sorted(results, key=lambda b: min(pt[0] for pt in b[0]))

            eu_cutoff_x = target_w * 0.12 # Alles vor 12% ist der blaue EU-Streifen
            max_prefix_x = target_w * 0.50 # Ortskürzel ist IMMER vor der Mitte (vor den Plaketten)

            candidates = []
            for bbox, text, conf in sorted_boxes:
                x_min = min(pt[0] for pt in bbox)
                clean = re.sub(r'[^A-ZÄÖÜ0-9]', '', text.upper())

                # Liegt die Textbox im linken Prefix-Bereich vor den Plaketten?
                if x_min >= (eu_cutoff_x - 12) and x_min <= max_prefix_x:
                    if 1 <= len(clean) <= 3:
                        candidates.append(clean)

            # 3. Falls die Vollbild-OCR nichts fand, isolierte Prefix-Zone probieren
            if not candidates:
                prefix_crop = scaled[:, int(eu_cutoff_x):int(max_prefix_x)]
                if prefix_crop.shape[1] > 15:
                    prefix_res = self.ocr.readtext(prefix_crop, allowlist='ABCDEFGHIJKLMNOPQRSTUVWXYZÄÖÜ', detail=0)
                    for t in prefix_res:
                        clean = re.sub(r'[^A-ZÄÖÜ]', '', t.upper())
                        if 1 <= len(clean) <= 3:
                            candidates.append(clean)

            matched_code = None
            matched_plate = None

            # Abgleich der linken Kandidaten gegen Kennzeichen-Stammdaten
            for tok in candidates:
                variants = [
                    tok,
                    tok.replace("0", "O").replace("1", "I").replace("8", "B"),
                    tok.replace("O", "0")
                ]
                for var in variants:
                    if alias_map and var in alias_map:
                        matched_code = var
                        matched_plate = alias_map[var]
                        break
                if matched_code:
                    break

            if matched_code:
                self.tracked_cache[grid_key] = {
                    "code": matched_code,
                    "plate": matched_plate,
                    "raw_text": f"[{matched_code}]",
                    "time": now
                }
                # Aus dem Fehler-Cache entfernen
                self.failed_cache.pop(grid_key, None)
            else:
                # Negativ-Cache: Wenn nicht erkannt, 1.5 Sekunden nicht erneut versuchen!
                self.failed_cache[grid_key] = now
        except Exception as e:
            print(f"[!] Background OCR Fehler: {e}")
            self.failed_cache[grid_key] = now
        finally:
            self.pending_crops.discard(grid_key)

    def detect(self, img_input: Any, conf_thresh: float = 0.22, imgsz: int = 480, alias_map: Dict[str, Any] = None) -> List[Dict[str, Any]]:
        """
        High-Speed Objekterkennung in ~25-30ms.
        Gibt sofort Bounding-Boxen zurück; OCR läuft asynchron im Hintergrund.
        """
        img, w, h = self._prepare_image(img_input)
        now = time.time()

        # Altes Cache aufräumen
        self.tracked_cache = {k: v for k, v in self.tracked_cache.items() if now - v["time"] < 4.0}
        self.failed_cache = {k: t for k, t in self.failed_cache.items() if now - t < 1.5}

        # 1. Ultra-schnelle YOLO-Inferenz (nur ~25-30ms)
        results = self.yolo(img, conf=conf_thresh, imgsz=imgsz, verbose=False)
        detections = []

        if not results or len(results[0].boxes) == 0:
            return detections

        for box in results[0].boxes:
            conf = float(box.conf[0])
            xyxy = box.xyxy[0].tolist()
            x1, y1, x2, y2 = map(int, xyxy)

            x1 = max(0, x1)
            y1 = max(0, y1)
            x2 = min(w, x2)
            y2 = min(h, y2)

            box_w = x2 - x1
            box_h = y2 - y1
            if box_w < 12 or box_h < 6:
                continue

            grid_key = self._get_grid_key(x1, y1, x2, y2, w, h)
            norm_box = {
                "x": round(x1 / w, 4),
                "y": round(y1 / h, 4),
                "w": round(box_w / w, 4),
                "h": round(box_h / h, 4)
            }

            cached = self.tracked_cache.get(grid_key)
            matched_code = cached["code"] if cached else None
            matched_plate = cached["plate"] if cached else None
            raw_text = cached["raw_text"] if cached else ""

            # Nur wenn:
            # - Noch nicht erkannt
            # - Noch kein OCR-Job in Arbeit
            # - Maximal 1 aktiver Job im Pool (Schutz vor CPU-Stau)
            # - Nicht im Negativ-Cooldown (letzter Fehlversuch vor > 1.5s)
            is_failed_recent = (now - self.failed_cache.get(grid_key, 0)) < 1.5
            if not cached and grid_key not in self.pending_crops and len(self.pending_crops) < 1 and not is_failed_recent:
                pad_x = int(box_w * 0.05)
                pad_y = int(box_h * 0.05)
                crop_x1 = max(0, x1 - pad_x)
                crop_y1 = max(0, y1 - pad_y)
                crop_x2 = min(w, x2 + pad_x)
                crop_y2 = min(h, y2 + pad_y)

                crop = img[crop_y1:crop_y2, crop_x1:crop_x2].copy()
                if crop.size > 0:
                    self.pending_crops.add(grid_key)
                    self.ocr_pool.submit(self._async_ocr_worker, crop, grid_key, alias_map)

            detections.append({
                "box": norm_box,
                "box_px": [x1, y1, x2, y2],
                "confidence": round(conf * 100, 1),
                "raw_text": raw_text,
                "matched_code": matched_code,
                "plate": matched_plate
            })

        return detections
