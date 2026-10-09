#!/usr/bin/env python3
"""
Kennzeichen Radar • Starter Script
Startet den lokalen Server und öffnet automatisch das Dashboard im Browser.
"""

import sys
import os
import socket
import webbrowser
from pathlib import Path

# UTF-8 Konsole
sys.stdout.reconfigure(encoding='utf-8')

def get_local_ip():
    """Ermittelt die lokale Netzwerk-IP für die Smartphone-Kamera."""
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
    port = 8080
    local_ip = get_local_ip()

    print("\n" + "=" * 65)
    print("   🚗  KENNZEICHEN-RADAR • KI-SCANNER & SAMMLER  🚗")
    print("=" * 65)
    print(f"💻 LAPTOP DASHBOARD:    http://localhost:{port}/")
    print(f"📱 HANDY-KAMERA HUD:    http://{local_ip}:{port}/cam")
    print("-" * 65)
    print("👉 Richte dein Smartphone auf vorausfahrende Autos.")
    print("👉 Fehlende Kennzeichen werden mit Konfidenz % live umrahmt")
    print("   und automatisch als Foto auf dem Laptop gespeichert!")
    print("=" * 65 + "\n")

    # Dashboard im Browser öffnen
    try:
        webbrowser.open(f"http://localhost:{port}/")
    except Exception:
        pass

    # Server starten
    from server import run
    run(port)

if __name__ == "__main__":
    main()
