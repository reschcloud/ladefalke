"""Basiskarte für die Heatmap aus Natural Earth (Public Domain) erzeugen – einmalig, Ergebnis liegt im Repo.

Quelle: https://github.com/nvkelso/natural-earth-vector (geojson/ne_50m_admin_0_countries.geojson,
geojson/ne_10m_admin_1_states_provinces.geojson, geojson/ne_10m_populated_places.geojson), dazu kleinere Orte aus
GeoNames (download.geonames.org/export/dump/cities5000.zip, CC-BY 4.0). Aufruf:
    python3 werkzeuge/basiskarte.py <admin_0.geojson> <admin_1.geojson> <populated_places.geojson> \
        <cities5000.txt> app/static/geo/basiskarte.js
Behalten werden europäische Länder sowie die Bundesländer von AT, DE und CH; Linien werden vereinfacht
(Douglas-Peucker) und Koordinaten auf 3 Nachkommastellen (~100 m) gerundet. Beschriftungen: Ländernamen (deutsch) an
der Natural-Earth-Beschriftungsstelle ab MIN_LABEL, Städte in Europa ab ihrem MIN_ZOOM (bis Zoomstufe ORTE_BIS_ZOOM).
Ergänzend GeoNames-Orte ab MINDESTEINWOHNER (je Land abweichend), Zoomstufe nach Einwohnerzahl; Orte in der Nähe eines
Natural-Earth-Orts entfallen (dort gibt es den deutschen Namen).
"""
import json
import sys

EUROPA = (-25.0, 34.0, 45.0, 72.0)  # lon_min, lat_min, lon_max, lat_max
REGIONEN_LAENDER = {"AT", "DE", "CH"}
ORTE_BIS_ZOOM = 8
ERGAENZUNGEN = [[48.204, 15.626, "St. Pölten", 6.1]]  # fehlt in Natural Earth (Landeshauptstadt)
MINDESTEINWOHNER = {"AT": 5000, "CH": 5000, "LI": 5000, "SI": 5000, "DE": 10000}
MINDESTEINWOHNER_SONST = 15000
ZOOM_NACH_EINWOHNERN = ((50000, 7), (15000, 8), (0, 9))
HAUPTORTE = {"PPLC", "PPLA", "PPLA2"}  # Hauptstadt, Landes- und Bezirkshauptorte: spätestens ab Zoom 8
OHNE = {"PPLX", "PPLH", "PPLW", "PPLQ"}  # Stadtteile, historische, zerstörte, verlassene Orte
NAEHE = 0.08  # Grad (~6–9 km): GeoNames-Ort entfällt, wenn ein Natural-Earth-Ort so nahe liegt


def _geonames_orte(pfad, vorhanden):
    """GeoNames cities5000 (Tab-getrennt): [lat, lon, name, ab_zoom, einwohner] ohne Orte nahe `vorhanden`."""
    raster = {(round(o[0] / NAEHE), round(o[1] / NAEHE)) for o in vorhanden}
    ergebnis = []
    for zeile in open(pfad, encoding="utf-8"):
        t = zeile.rstrip("\n").split("\t")
        lat, lon, art, land, einwohner = float(t[4]), float(t[5]), t[7], t[8], int(t[14] or 0)
        if (not _im_ausschnitt(lon, lat) or art in OHNE
                or einwohner < MINDESTEINWOHNER.get(land, MINDESTEINWOHNER_SONST)):
            continue
        zelle = (round(lat / NAEHE), round(lon / NAEHE))
        if any((zelle[0] + a, zelle[1] + b) in raster for a in (-1, 0, 1) for b in (-1, 0, 1)):
            continue
        ab = next(z for grenze, z in ZOOM_NACH_EINWOHNERN if einwohner >= grenze)
        # GeoNames-Einwohnerzahlen sind nicht immer verlässlich: Hauptorte spätestens, andere frühestens ab Zoom 8
        ab = min(ab, 8) if art in HAUPTORTE else max(ab, 8)
        ergebnis.append([round(lat, 3), round(lon, 3), t[1], ab, einwohner])
    return ergebnis


def _im_ausschnitt(lon, lat):
    return EUROPA[0] <= lon <= EUROPA[2] and EUROPA[1] <= lat <= EUROPA[3]


def _abstand(p, a, b):
    if a == b:
        return ((p[0] - a[0]) ** 2 + (p[1] - a[1]) ** 2) ** 0.5
    dx, dy = b[0] - a[0], b[1] - a[1]
    t = max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / (dx * dx + dy * dy)))
    return ((p[0] - a[0] - t * dx) ** 2 + (p[1] - a[1] - t * dy) ** 2) ** 0.5


def vereinfachen(punkte, toleranz):
    if len(punkte) < 3:
        return punkte
    behalten, stapel = [False] * len(punkte), [(0, len(punkte) - 1)]
    behalten[0] = behalten[-1] = True
    while stapel:
        anfang, ende = stapel.pop()
        index, groesster = None, toleranz
        for i in range(anfang + 1, ende):
            d = _abstand(punkte[i], punkte[anfang], punkte[ende])
            if d > groesster:
                index, groesster = i, d
        if index is not None:
            behalten[index] = True
            stapel += [(anfang, index), (index, ende)]
    return [p for p, b in zip(punkte, behalten) if b]


def _ring(ring, toleranz):
    r = [[round(x, 3), round(y, 3)] for x, y in vereinfachen([tuple(p[:2]) for p in ring], toleranz)]
    return r if len(r) >= 4 else None


def _geometrie(g, toleranz):
    polygone = [g["coordinates"]] if g["type"] == "Polygon" else g["coordinates"]
    ergebnis = []
    for polygon in polygone:
        ringe = [r for r in (_ring(ring, toleranz) for ring in polygon) if r]
        if ringe:
            ergebnis.append(ringe)
    return {"type": "MultiPolygon", "coordinates": ergebnis} if ergebnis else None


def _in_europa(g):
    pts = [p for poly in ([g["coordinates"]] if g["type"] == "Polygon" else g["coordinates"]) for p in poly[0]]
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    return max(xs) >= EUROPA[0] and min(xs) <= EUROPA[2] and max(ys) >= EUROPA[1] and min(ys) <= EUROPA[3]


def main(admin0, admin1, orte_datei, geonames, ziel):
    laender, regionen, laendernamen, orte = [], [], [], []
    for f in json.load(open(admin0, encoding="utf-8"))["features"]:
        g, p = f["geometry"], f["properties"]
        if g and _in_europa(g) and (geo := _geometrie(g, 0.02)):
            laender.append({"type": "Feature", "properties": {"iso": p.get("ISO_A2_EH")}, "geometry": geo})
            if p.get("LABEL_X") is not None and _im_ausschnitt(p["LABEL_X"], p["LABEL_Y"]):
                laendernamen.append([round(p["LABEL_Y"], 3), round(p["LABEL_X"], 3), p.get("NAME_DE") or p["NAME"],
                                     p.get("MIN_LABEL") or 3])
    for f in json.load(open(orte_datei, encoding="utf-8"))["features"]:
        p, (lon, lat) = f["properties"], f["geometry"]["coordinates"][:2]
        if _im_ausschnitt(lon, lat) and (p.get("MIN_ZOOM") or 99) <= ORTE_BIS_ZOOM:
            orte.append([round(lat, 3), round(lon, 3), p.get("NAME_DE") or p["NAME"], p["MIN_ZOOM"], p.get("POP_MAX") or 0])
    orte += [o + [0] for o in ERGAENZUNGEN]
    orte += _geonames_orte(geonames, orte)
    orte.sort(key=lambda o: (o[3], -o[4]))  # wichtigste zuerst – werden bei Überlappung bevorzugt
    orte = [o[:4] for o in orte]
    for f in json.load(open(admin1, encoding="utf-8"))["features"]:
        if f["properties"].get("iso_a2") in REGIONEN_LAENDER and f["geometry"] and (geo := _geometrie(f["geometry"], 0.01)):
            regionen.append({"type": "Feature", "properties": {"name": f["properties"].get("name")}, "geometry": geo})
    ergebnis = {"quelle": "Natural Earth (Public Domain), vereinfacht",
                "laender": {"type": "FeatureCollection", "features": laender},
                "regionen": {"type": "FeatureCollection", "features": regionen},
                "laendernamen": laendernamen,  # [lat, lon, name, ab_zoom]
                "orte": orte}  # [lat, lon, name, ab_zoom]
    with open(ziel, "w", encoding="utf-8") as datei:  # als Skript: sofort verfügbar, vom Browser gecacht
        datei.write("window.BASISKARTE = ")
        json.dump(ergebnis, datei, ensure_ascii=False, separators=(",", ":"))
        datei.write(";\n")
    print(f"{len(laender)} Länder, {len(regionen)} Regionen, {len(orte)} Orte → {ziel}")


if __name__ == "__main__":
    main(*sys.argv[1:6])
