"""Kartendetails aus Natural Earth (Public Domain): Straßen, Flüsse, Seen und schattiertes Relief – einmalig erzeugt,
Ergebnis liegt im Repo (app/static/geo/details.json, app/static/geo/relief.jpg).

Quellen: https://github.com/nvkelso/natural-earth-vector (geojson/ne_10m_roads, ne_10m_rivers_lake_centerlines,
ne_10m_rivers_europe, ne_10m_lakes, ne_10m_lakes_europe) und https://naciscdn.org/naturalearth/10m/raster/SR_HR.zip
(Shaded Relief, entpackt). Braucht Pillow und numpy. Aufruf mit dem Verzeichnis der Quelldateien:
    python3 werkzeuge/kartendetails.py data/geo/natural_earth app/static/geo
Ausschnitt wie die Basiskarte (Europa). Linien werden vereinfacht und auf 3 Nachkommastellen gerundet; jedes Objekt
bekommt die Zoomstufe, ab der es gezeigt wird (Natural Earth „min_zoom“). Das Relief wird von der
geografischen Projektion der Quelle in Web-Mercator (Leaflet) umgerechnet; Flächen ohne Neigung werden weiß.
"""
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from basiskarte import EUROPA, vereinfachen  # noqa: E402

PX_JE_GRAD = 60  # Auflösung des Reliefs (wie die Quelle, ca. 1,8 km je Pixel)


def _linien(g):
    if g["type"] == "LineString":
        return [g["coordinates"]]
    if g["type"] == "MultiLineString":
        return g["coordinates"]
    return []


def _ringe(g):
    polys = [g["coordinates"]] if g["type"] == "Polygon" else g["coordinates"] if g["type"] == "MultiPolygon" else []
    return [p[0] for p in polys]  # nur Außenringe – Inseln in Seen sind hier unerheblich


def _im_ausschnitt(punkte):
    return any(EUROPA[0] <= x <= EUROPA[2] and EUROPA[1] <= y <= EUROPA[3] for x, y in (p[:2] for p in punkte))


def _kurz(punkte, toleranz):
    r = [[round(x, 3), round(y, 3)] for x, y in vereinfachen([tuple(p[:2]) for p in punkte], toleranz)]
    return r if len(r) >= 2 else None


def _min_zoom(p, standard):
    z = p.get("min_zoom")
    return round(float(z), 1) if z not in (None, "") else standard


def strassen(quelle):
    """[art, ab_zoom, [[lon, lat], …]] – art: autobahn | hauptstrasse | strasse; Fähren entfallen."""
    ergebnis = []
    for f in json.load(open(quelle / "ne_10m_roads.geojson", encoding="utf-8"))["features"]:
        p = f["properties"]
        if p.get("continent") != "Europe" or "Ferry" in (p.get("type") or "") or not f["geometry"]:
            continue
        art = ("autobahn" if p.get("expressway") == 1 or p.get("type") == "Major Highway"
               else "hauptstrasse" if p.get("type") == "Secondary Highway" else "strasse")
        for linie in _linien(f["geometry"]):
            if _im_ausschnitt(linie) and (k := _kurz(linie, 0.004)):
                ergebnis.append([art, _min_zoom(p, 6), k])
    return ergebnis


def fluesse(quelle):
    """[ab_zoom, breite (1–3), [[lon, lat], …]] aus den weltweiten Flüssen und dem Europa-Zusatz."""
    ergebnis = []
    for datei, standard in (("ne_10m_rivers_lake_centerlines.geojson", 5), ("ne_10m_rivers_europe.geojson", 7)):
        for f in json.load(open(quelle / datei, encoding="utf-8"))["features"]:
            p, g = f["properties"], f["geometry"]
            if not g or p.get("featurecla", "").startswith("Lake Centerline"):
                continue  # Mittellinien durch Seen – die Seen werden als Fläche gezeichnet
            rang = p.get("scalerank") or 10
            breite = 3 if rang <= 3 else 2 if rang <= 6 else 1
            for linie in _linien(g):
                if _im_ausschnitt(linie) and (k := _kurz(linie, 0.003)):
                    ergebnis.append([_min_zoom(p, standard), breite, k])
    return ergebnis


def seen(quelle):
    """[ab_zoom, [[lon, lat], …]] – Außenring je See."""
    ergebnis = []
    for datei, standard in (("ne_10m_lakes.geojson", 4), ("ne_10m_lakes_europe.geojson", 7)):
        for f in json.load(open(quelle / datei, encoding="utf-8"))["features"]:
            if not f["geometry"]:
                continue
            for ring in _ringe(f["geometry"]):
                if _im_ausschnitt(ring) and (k := _kurz(ring, 0.003)) and len(k) >= 4:
                    ergebnis.append([_min_zoom(f["properties"], standard), k])
    return ergebnis


def _merc(lat):
    return math.log(math.tan(math.pi / 4 + math.radians(lat) / 2))


def relief(tif, ziel):
    """Ausschnitt EUROPA aus SR_HR (Plate carrée, 21600×10800) → Web-Mercator, Graustufen-JPEG."""
    import numpy as np
    from PIL import Image, ImageFilter
    Image.MAX_IMAGE_PIXELS = None
    quelle = np.asarray(Image.open(tif).convert("L"))
    hoehe, breite = quelle.shape
    lon0, lat0, lon1, lat1 = EUROPA
    spalten = np.arange(int((lon0 + 180) / 360 * breite), int((lon1 + 180) / 360 * breite))
    w = len(spalten)
    massstab = w / math.radians(lon1 - lon0)  # Pixel je Radiant
    h = round((_merc(lat1) - _merc(lat0)) * massstab)
    y = _merc(lat1) - (np.arange(h) + 0.5) / massstab  # Mercator-y je Ausgabezeile (oben = Norden)
    lat = np.degrees(2 * np.arctan(np.exp(y)) - math.pi / 2)
    zeilen = np.clip(((90 - lat) / 180 * hoehe).astype(int), 0, hoehe - 1)
    ausschnitt = quelle[np.ix_(zeilen, spalten)].astype(float)
    # Flächen ohne Neigung (Meer, Ebenen) haben einen einheitlichen Grauwert (Median) → weiß; übrig bleiben nur die
    # Schatten der Hänge – das Bild wird in der Karte multipliziert und lässt Meer und Ebenen unverändert
    flach = float(np.median(ausschnitt))
    bild = Image.fromarray(np.clip(ausschnitt * 255 / flach, 0, 255).astype(np.uint8))
    # leicht geglättet und hohe Qualität: beim Hineinzoomen wird das Bild stark vergrößert, JPEG-Blöcke fielen auf
    bild.filter(ImageFilter.GaussianBlur(0.8)).save(ziel, quality=88, optimize=True, progressive=True)
    return bild.size


def main(quelle, ziel):
    quelle, ziel = Path(quelle), Path(ziel)
    daten = {"quelle": "Natural Earth (Public Domain), vereinfacht", "strassen": strassen(quelle),
             "fluesse": fluesse(quelle), "seen": seen(quelle),
             "relief": {"bild": "relief.jpg", "grenzen": [[EUROPA[1], EUROPA[0]], [EUROPA[3], EUROPA[2]]]}}
    with open(ziel / "details.json", "w", encoding="utf-8") as datei:
        json.dump(daten, datei, ensure_ascii=False, separators=(",", ":"))
    groesse = relief(quelle / "sr_hr" / "SR_HR.tif", ziel / "relief.jpg")
    print(f"{len(daten['strassen'])} Straßen, {len(daten['fluesse'])} Flüsse, {len(daten['seen'])} Seen, "
          f"Relief {groesse[0]}×{groesse[1]} → {ziel}")


if __name__ == "__main__":
    main(*sys.argv[1:3])
