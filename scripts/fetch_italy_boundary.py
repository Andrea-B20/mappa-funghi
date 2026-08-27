"""
Scarica il confine geografico reale dell'Italia (terraferma + isole) da
Nominatim/OpenStreetMap e lo salva in cache locale. Usato per escludere
dalla mappa i punti che cadono in mare o in paesi confinanti.

Uso:
    .venv/bin/python scripts/fetch_italy_boundary.py
"""

import json
import time
from pathlib import Path

import requests

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
OUT_PATH = Path(__file__).resolve().parent.parent / "data" / "italy_boundary.geojson"

# Nominatim richiede un User-Agent identificativo (uso non commerciale, un
# fetch una tantum per un prototipo personale — rispettiamo il rate limit
# di 1 richiesta/secondo della loro usage policy).
HEADERS = {"User-Agent": "mappa-funghi-prototype/0.1 (uso personale/hobby)"}


def main():
    params = {
        "country": "Italy",
        "polygon_geojson": 1,
        "format": "json",
        "limit": 1,
    }
    resp = requests.get(NOMINATIM_URL, params=params, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    results = resp.json()
    if not results:
        raise SystemExit("Nessun risultato da Nominatim per 'Italy'")

    geometry = results[0]["geojson"]
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(geometry), encoding="utf-8")
    print(f"Confine salvato: {OUT_PATH} (tipo: {geometry['type']})")
    time.sleep(1)  # cortesia verso l'API pubblica


if __name__ == "__main__":
    main()
