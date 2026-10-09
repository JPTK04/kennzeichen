# 🚗 Kennzeichen Radar • KI-Scanner & Sammler

Ein eigenständiges, lokales Tool für Kennzeichen-Sammler: Vom Entschlüsseln des Android-App-Backups bis zur vollautomatischen **Live-KI-Kennzeichenerkennung (YOLOv8 & ALPR)** per Smartphone-Kamera während der Autofahrt.

![Python](https://img.shields.io/badge/Python-3.10+-3776AB?logo=python&logoColor=white)
![YOLOv8](https://img.shields.io/badge/AI-YOLOv8%20Object%20Detection-00FFFF)
![ALPR](https://img.shields.io/badge/ALPR-EasyOCR-green)
![License](https://img.shields.io/badge/License-MIT-blue)

---

## ✨ Was kann das Tool?

1. **App-Backups automatisch entschlüsseln & abgleichen:**
   - Liest binäre Android-App-Backups (`zlib` komprimiert mit `KX1`-Header) oder SQLite-Datenbanken ein.
   - Gleicht die Sammlung in Echtzeit mit allen **1.069 deutschen Zulassungsbezirken** (inkl. Altkennzeichen & Diplomaten) ab.
   - Zeigt sekundenschnell: *Wie viele fehlen mir noch? Welche Bundesländer sind fast voll? Wieviele Sammler-Punkte habe ich?*

2. **🤖 YOLOv8 KI-Objekterkennung (ALPR):**
   - **Keine Fehlalarme mehr durch Rillen oder Schilder**: Erkennt Kennzeichen vorausfahrender Fahrzeuge auf Autobahn und Landstraße zuverlässig als geometrisches Objekt.
   - Liefert passgenaue Bounding-Boxes mit **Konfidenz-Prozentwert** (z. B. `[ESB 92%]`).
   - Schneidet das Kennzeichen pixelgenau aus, schärft den Kontrast und liest das Kürzel per OCR aus.

3. **📱 Smartphone als Dashcam nutzen:**
   - Kein App-Installieren auf dem Handy nötig: Einfach im Handy-Browser `http://<LAPTOP-IP>:8080/cam` öffnen.
   - Reines Vollbild-HUD: Zeigt das Live-Kamerabild mit leuchtenden KI-Zielrahmen (**Grün** = Neuer Fund!, **Blau** = Bereits gesammelt).
   - Akustischer Signalton (Victory Chime) und Vibrations-Feedback bei neu entdeckten Kennzeichen.

4. **📸 Automatische Beweisfotos & Trip-Logger:**
   - Sobald ein noch fehlendes Kennzeichen erkannt wird, speichert das System ein hochauflösendes Foto mit grünem Rahmen, Kennzeichen-Kürzel, Stadt/Landkreis und Zeitstempel im Ordner `captures/`.
   - Alle Treffer des Roadtrips werden in einer Merkliste gesammelt und können mit einem Klick als CSV exportiert werden, um sie später entspannt in die App einzutragen.

---

## 🚀 Schnellstart

### 1. Repository klonen & Abhängigkeiten installieren
```bash
git clone https://github.com/JPTK04/kennzeichen.git
cd kennzeichen

pip install -r requirements.txt
```

### 2. Starten
```bash
python run.py
```
`run.py` ermittelt automatisch deine lokale Netzwerk-IP und öffnet das Dashboard direkt im Browser.

- **Laptop-Dashboard:** `http://localhost:8080/`
- **Handy-Kamera:** `http://<Deine-WLAN-IP>:8080/cam`

---

## 📁 Projektstruktur

```text
kennzeichen/
├── data/
│   └── data.db                      # SQLite-Stammdaten der 1.069 deutschen Kennzeichen
├── models/
│   └── license_plate_detector.pt   # Trainiertes YOLOv8 Kennzeichen-Erkennungsmodell
├── captures/                        # Automatisch gespeicherte Beweisfotos mit grünem Rahmen
├── web/
│   ├── index.html                   # Desktop-Dashboard (Statistik, Bundesländer, Trip-Log)
│   ├── cam.html                     # Smartphone Kamera-HUD (Vollbild, KI-Overlay)
│   ├── app.js                       # Dashboard-Logik
│   ├── cam.js                       # Live-Streaming & dynamische KI-Bounding-Boxes
│   ├── style.css                    # Modernes Dark-Mode & Glassmorphism Design
│   └── tesseract.min.js             # Client-seitiges OCR-Fallback
├── detector.py                      # YOLOv8 Inferenz, Bildoptimierung & Schrifterkennung
├── server.py                        # REST-API Server (Backup-Parser, KI-Detection, Foto-Speicherung)
├── run.py                           # 1-Klick-Starter mit automatischer IP-Erkennung
├── requirements.txt                 # Python-Abhängigkeiten
└── README.md
```

---

## 🛠️ REST API Endpunkte

| Methode | Pfad | Beschreibung |
|---|---|---|
| `GET` | `/api/data` | Gibt alle Kennzeichen, Statistiken, Bundesländer und den aktuellen Trip-Log zurück |
| `POST` | `/api/detect` | Führt YOLOv8-Erkennung auf einem übergebenen Frame aus und liefert Bounding-Boxes & Kürzel |
| `POST` | `/api/capture` | Speichert ein gerahmtes Beweisfoto im Ordner `captures/` und erfasst den Fund im Trip-Log |
| `POST` | `/api/upload` | Lädt eine Android-Backup-Datei (`KX1` / `zlib` / `.db`) hoch und aktualisiert den Sammlungsstatus |
| `GET` | `/api/trip/export` | Exportiert die heutige Trip-Liste als formatierte CSV-Datei |

---

## ⚖️ Lizenz
MIT License. Erstellt für passionierte Kennzeichensammler.
