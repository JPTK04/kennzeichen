#!/usr/bin/env python3
"""
Kennzeichen Radar • Starter Script
Wähle zwischen dem ultra-schnellen Offline-Kamera Scanner (USB) und dem Web-Server.
"""

import sys
import os
import socket
import webbrowser
from pathlib import Path

# UTF-8 Konsole
sys.stdout.reconfigure(encoding='utf-8')

def get_local_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(('8.8.8.8', 80))
        ip = s.getsockname()[0]
    except Exception:
        ip = '127.0.0.1'
    finally:
        s.close()
    return ip

def main():
    args = sys.argv[1:]

    if len(args) > 0 and args[0] in ("cam", "live", "camera", "usb"):
        from live_cam import LiveRadarApp
        cam_id = int(args[1]) if len(args) > 1 else 0
        app = LiveRadarApp(cam_index=cam_id)
        app.run()
        return

    port = 8080
    local_ip = get_local_ip()

    print("\n" + "=" * 68)
    print("      🚗  KENNZEICHEN-RADAR • KI-SCANNER & SAMMLER  🚗")
    print("=" * 68)
    print("  Wähle deinen Modus:")
    print("  [1] ⚡ High-Speed Offline-Live-Scanner (USB-Handy / Webcam)")
    print("       -> 30+ FPS, direkte Hardware-Erkennung, KEIN WLAN/Hotspot nötig!")
    print(f"  [2] 🌐 Web-Server & Browser-Dashboard (http://localhost:{port}/)")
    print(f"       -> Handy per WLAN verbinden: http://{local_ip}:{port}/cam")
    print("=" * 68)

    try:
        choice = input("\n👉 Auswahl [1/2] (Standard: 1 für USB/Kamera): ").strip()
    except (KeyboardInterrupt, EOFError):
        choice = "1"

    if choice in ("2", "web", "server"):
        try:
            webbrowser.open(f"http://localhost:{port}/")
        except Exception:
            pass
        from server import run
        run(port)
    else:
        from live_cam import LiveRadarApp
        app = LiveRadarApp(cam_index=0)
        app.run()

if __name__ == "__main__":
    main()
