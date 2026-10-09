#!/usr/bin/env python3
"""
High-Speed KI-Kennzeichenerkennung (ALPR) mit YOLOv8 & Asynchronem OCR
Optimiert für 30+ FPS Echtzeit-Tracking auf Intel Core i7 / Multi-Core CPUs.
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
# Alle Performance-Cores des i7 nutzen
torch.set_num_threads(8)

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
        self.ocr_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="PlateOCR")

        # Cache für aktive Kennzeichen: { grid_key: { "code", "plate", "timestamp" } }
        self.tracked_cache = {}
        self.pending_crops = set()
        print("[KI] High-Speed Pipeline mit 30+ FPS Tracking bereit!")

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
        """Läuft im Hintergrundthread: Liest Text vom Kennzeichen ohne den Videostream zu blockieren."""
        try:
            ch, cw = crop.shape[:2]
            if ch < 50 or cw < 160:
                scale = max(50 / ch, 160 / cw)
                crop_proc = cv2.resize(crop, (int(cw * scale), int(ch * scale)), interpolation=cv2.INTER_CUBIC)
            else:
                crop_proc = crop

            gray = cv2.cvtColor(crop_proc, cv2.COLOR_BGR2GRAY)
            clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(6, 6))
            enhanced = clahe.apply(gray)

            ocr_results = self.ocr.readtext(enhanced, detail=0)
            raw_text = " ".join(ocr_results).upper()

            matched_code = None
            matched_plate = None

            if alias_map and raw_text:
                cleaned = re.sub(r'[^A-ZÄÖÜ0-9]', ' ', raw_text)
                tokens = [t for t in cleaned.split() if 1 <= len(t) <= 3 and re.match(r'^[A-ZÄÖÜ]+$', t)]
                for tok in tokens:
                    if tok in alias_map:
                        matched_code = tok
                        matched_plate = alias_map[tok]
                        break

            if matched_code:
                self.tracked_cache[grid_key] = {
                    "code": matched_code,
                    "plate": matched_plate,
                    "raw_text": raw_text,
                    "time": time.time()
                }
        except Exception as e:
            print(f"[!] Background OCR Fehler: {e}")
        finally:
            self.pending_crops.discard(grid_key)

    def detect(self, img_input: Any, conf_thresh: float = 0.22, imgsz: int = 480, alias_map: Dict[str, Any] = None) -> List[Dict[str, Any]]:
        """
        High-Speed Objekterkennung in ~25-30ms.
        Gibt sofort Bounding-Boxen zurück; OCR läuft asynchron im Hintergrund.
        """
        img, w, h = self._prepare_image(img_input)
        now = time.time()

        # Altes Cache aufräumen (> 4 Sekunden)
        self.tracked_cache = {k: v for k, v in self.tracked_cache.items() if now - v["time"] < 4.0}

        # 1. Ultra-schnelle YOLO-Inferenz (nur 25ms auf Intel CPU)
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

            # Prüfen ob dieses Kennzeichen an dieser Position bereits erkannt wurde
            cached = self.tracked_cache.get(grid_key)
            matched_code = cached["code"] if cached else None
            matched_plate = cached["plate"] if cached else None
            raw_text = cached["raw_text"] if cached else ""

            # Falls noch nicht erkannt und noch nicht im Hintergrundjob -> Asynchron starten!
            if not cached and grid_key not in self.pending_crops:
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
