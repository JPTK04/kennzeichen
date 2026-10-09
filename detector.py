#!/usr/bin/env python3
"""
KI-gestützte Kennzeichenerkennung (ALPR) mit YOLOv8 & EasyOCR
Lokal, schnell und millimetergenau für den Straßenverkehr.
"""

import os
import sys
import re
import io
import base64
import warnings
from pathlib import Path
from typing import List, Dict, Any, Tuple, Optional

# Warnungen unterdrücken für saubere Konsole
warnings.filterwarnings("ignore", category=UserWarning)

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

# UTF-8 Konsole sicherstellen
sys.stdout.reconfigure(encoding='utf-8')

BASE_DIR = Path(__file__).resolve().parent
MODEL_PATH = BASE_DIR / "models" / "license_plate_detector.pt"

class LicensePlateDetector:
    _instance = None

    def __init__(self, model_path: Optional[Path] = None):
        from ultralytics import YOLO
        import easyocr

        self.model_path = model_path or MODEL_PATH
        if not self.model_path.is_file():
            raise FileNotFoundError(f"YOLO-Modell nicht gefunden unter: {self.model_path}")

        print(f"[KI] Lade YOLOv8 Kennzeichen-Detektor ({self.model_path.name})...")
        self.yolo = YOLO(str(self.model_path))

        print("[KI] Lade EasyOCR Schrifterkennung...")
        self.ocr = easyocr.Reader(['de', 'en'], gpu=False, verbose=False)
        print("[KI] Pipeline erfolgreich einsatzbereit!")

    @classmethod
    def get_instance(cls):
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def _prepare_image(self, img_input: Any) -> Tuple[np.ndarray, int, int]:
        """Konvertiert verschiedene Bildeingaben (Bytes, PIL, Pfad) in ein BGR-Numpy-Array."""
        if isinstance(img_input, (str, Path)):
            img = cv2.imread(str(img_input))
            if img is None:
                raise ValueError(f"Bild konnte nicht geladen werden: {img_input}")
        elif isinstance(img_input, bytes):
            nparr = np.frombuffer(img_input, np.uint8)
            img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
            if img is None:
                raise ValueError("Ungültige Bild-Bytes")
        elif isinstance(img_input, Image.Image):
            img = cv2.cvtColor(np.array(img_input), cv2.COLOR_RGB2BGR)
        elif isinstance(img_input, np.ndarray):
            img = img_input
        else:
            raise TypeError(f"Nicht unterstützter Bildtyp: {type(img_input)}")

        h, w = img.shape[:2]
        return img, w, h

    def detect(self, img_input: Any, conf_thresh: float = 0.25, imgsz: int = 640, alias_map: Dict[str, Any] = None) -> List[Dict[str, Any]]:
        """
        Scannt das Bild nach Kennzeichen.
        Gibt eine Liste von Funden zurück inkl. Bounding-Box, Konfidenz und extrahiertem Kürzel.
        """
        img, w, h = self._prepare_image(img_input)
        
        # 1. YOLO Objekterkennung
        results = self.yolo(img, conf=conf_thresh, imgsz=imgsz, verbose=False)
        detections = []

        if not results or len(results[0].boxes) == 0:
            return detections

        for box in results[0].boxes:
            conf = float(box.conf[0])
            xyxy = box.xyxy[0].tolist()
            x1, y1, x2, y2 = map(int, xyxy)

            # Bereich absichern
            x1 = max(0, x1)
            y1 = max(0, y1)
            x2 = min(w, x2)
            y2 = min(h, y2)

            box_w = x2 - x1
            box_h = y2 - y1
            if box_w < 10 or box_h < 6:
                continue

            # Relativer Ausschnitt (0.0 bis 1.0)
            norm_box = {
                "x": round(x1 / w, 4),
                "y": round(y1 / h, 4),
                "w": round(box_w / w, 4),
                "h": round(box_h / h, 4)
            }

            # 2. Kennzeichen zuschneiden (mit 5% Rand für optimale Lesbarkeit)
            pad_x = int(box_w * 0.05)
            pad_y = int(box_h * 0.05)
            crop_x1 = max(0, x1 - pad_x)
            crop_y1 = max(0, y1 - pad_y)
            crop_x2 = min(w, x2 + pad_x)
            crop_y2 = min(h, y2 + pad_y)

            crop = img[crop_y1:crop_y2, crop_x1:crop_x2]
            if crop.size == 0:
                continue

            # Bei kleinen Kennzeichen: intelligente Skalierung für scharfe Kanten
            ch, cw = crop.shape[:2]
            if ch < 60 or cw < 200:
                scale = max(60 / ch, 200 / cw)
                new_w = int(cw * scale)
                new_h = int(ch * scale)
                crop_enhanced = cv2.resize(crop, (new_w, new_h), interpolation=cv2.INTER_CUBIC)
            else:
                crop_enhanced = crop

            # Kontrast optimieren
            gray = cv2.cvtColor(crop_enhanced, cv2.COLOR_BGR2GRAY)
            # Adaptiver Kontrast
            clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
            enhanced = clahe.apply(gray)

            # 3. OCR Schrifterkennung auf dem zugeschnittenen Kennzeichen
            try:
                ocr_results = self.ocr.readtext(enhanced, detail=0)
                raw_text = " ".join(ocr_results).upper()
            except Exception as e:
                print(f"[!] OCR Fehler auf Crop: {e}")
                raw_text = ""

            # 4. Deutsches Kennzeichen-Kürzel isolieren
            matched_plate = None
            matched_code = None

            if alias_map and raw_text:
                # Text bereinigen (nur A-Z und Zahlen)
                cleaned = re.sub(r'[^A-ZÄÖÜ0-9]', ' ', raw_text)
                tokens = [t for t in cleaned.split() if 1 <= len(t) <= 3 and re.match(r'^[A-ZÄÖÜ]+$', t)]
                
                # Erstes passendes deutsches Kürzel suchen
                for tok in tokens:
                    if tok in alias_map:
                        matched_code = tok
                        matched_plate = alias_map[tok]
                        break

            # Crop als Mini-Vorschau (JPEG Base64)
            _, buffer = cv2.imencode('.jpg', crop, [cv2.IMWRITE_JPEG_QUALITY, 85])
            crop_b64 = base64.b64encode(buffer).decode('utf-8')

            detections.append({
                "box": norm_box,
                "box_px": [x1, y1, x2, y2],
                "confidence": round(conf * 100, 1),
                "raw_text": raw_text,
                "matched_code": matched_code,
                "plate": matched_plate,
                "crop_preview": f"data:image/jpeg;base64,{crop_b64}"
            })

        return detections

    def draw_detections(self, img_input: Any, detections: List[Dict[str, Any]], default_status: str = "missing") -> np.ndarray:
        """
        Zeichnet die erkannten Bounding-Boxes mit KI-Konfidenz und Kennzeichen-Badge
        direkt in das hochauflösende Bild ein (für das Beweisfoto).
        """
        img, w, h = self._prepare_image(img_input)
        annotated = img.copy()

        for det in detections:
            x1, y1, x2, y2 = det["box_px"]
            conf = det["confidence"]
            code = det.get("matched_code")
            plate = det.get("plate")

            status = default_status
            if plate and isinstance(plate, dict):
                status = plate.get("status", default_status)

            # Farbe: Grün für neue/fehlende Kennzeichen, Blau für bereits gesehene
            color_bgr = (46, 204, 113) if status == "missing" else (241, 196, 15)
            color_rgb = (113, 204, 46) if status == "missing" else (15, 196, 241)

            # Bounding Box
            cv2.rectangle(annotated, (x1, y1), (x2, y2), color_bgr, max(3, int(w * 0.004)))

            # Beschriftungs-Banner
            label = f"[{code or 'KFZ'}] {conf:.0f}%"
            if plate and "stadt_kreis" in plate:
                label += f" • {plate['stadt_kreis']}"

            # Banner-Größe
            font_scale = max(0.5, w / 1600.0)
            thickness = max(1, int(w * 0.002))
            (tw, th), baseline = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, font_scale, thickness)

            banner_y1 = max(0, y1 - th - 12)
            banner_y2 = y1
            cv2.rectangle(annotated, (x1, banner_y1), (x1 + tw + 16, banner_y2), color_bgr, -1)
            cv2.putText(annotated, label, (x1 + 8, y1 - 6), cv2.FONT_HERSHEY_SIMPLEX, font_scale, (0, 0, 0), thickness, cv2.LINE_AA)

        return annotated
