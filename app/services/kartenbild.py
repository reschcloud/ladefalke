"""Statisches Kartenbild mit Heatmap für die PDF-Reports – aus der lokalen Grundkarte, ohne fremde Dienste.

Grundkarte: static/geo/basiskarte.js (Natural Earth: Länder, Bundesländer, Ländernamen, Orte), Web-Mercator wie die
Karte der App, Farben wie deren helle Darstellung. Die Heatmap entsteht aus den Ladeorten [(lat, lon, anzahl)]: je Ort
ein weicher Fleck (Intensität logarithmisch zur Anzahl), weichgezeichnet, eingefärbt mit dem Verlauf der App
(--karte-heat). Ausschnitt: alle Orte mit Rand, mindestens etwa 1,5° breit. Ergebnis: PNG (bzw. data-URI fürs PDF).
"""
import base64
import io
import json
import math
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

GRUNDKARTE = Path(__file__).resolve().parent.parent / "static" / "geo" / "basiskarte.js"
WASSER, LAND, GRENZE, REGION, TEXT = "#e6eef6", "#f7f7f5", "#b3b2a7", "#dcdbd4", "#373c44"
HEAT = ["#f7c4dc", "#ec7fae", "#d6336c", "#93307f", "#5b2a86"]  # wie --karte-heat (hell)
SCHRIFTEN = ("/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
             "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")


@lru_cache
def _grundkarte() -> dict:
    text = GRUNDKARTE.read_text()
    return json.loads(text[text.index("{"):text.rindex("}") + 1])


def _merc(lat: float, lon: float) -> tuple[float, float]:
    lat = max(-85.0, min(85.0, lat))
    return math.radians(lon), math.log(math.tan(math.pi / 4 + math.radians(lat) / 2))


def _schrift(groesse: int):
    for pfad in SCHRIFTEN:
        try:
            return ImageFont.truetype(pfad, groesse)
        except OSError:
            continue
    return ImageFont.load_default(size=groesse)


def _farbe(hexwert: str) -> tuple[int, int, int]:
    return tuple(int(hexwert[i:i + 2], 16) for i in (1, 3, 5))


def _verlauf() -> tuple[list[int], list[int], list[int], list[int]]:
    """Farbtabellen (je Kanal 256 Werte) für die Einfärbung der Heatmap; schwache Werte durchsichtig."""
    stopps = [(0.15 + 0.85 * i / (len(HEAT) - 1), _farbe(f)) for i, f in enumerate(HEAT)]
    r, g, b, a = [], [], [], []
    for v in range(256):
        t = v / 255
        if t <= stopps[0][0]:
            farbe = stopps[0][1]
        else:
            for (t0, f0), (t1, f1) in zip(stopps, stopps[1:]):
                if t <= t1:
                    k = (t - t0) / (t1 - t0)
                    farbe = tuple(round(x0 + (x1 - x0) * k) for x0, x1 in zip(f0, f1))
                    break
        r.append(farbe[0]), g.append(farbe[1]), b.append(farbe[2])
        a.append(0 if t < 0.03 else round(255 * min(0.9, 0.35 + 0.7 * t)))
    return r, g, b, a


def bild(punkte: list[tuple[float, float, int]], breite: int = 1600, hoehe: int = 950) -> bytes | None:
    """PNG der Heatmap über der Grundkarte; None ohne Punkte."""
    if not punkte:
        return None
    xs, ys = zip(*(_merc(lat, lon) for lat, lon, _ in punkte))
    mitte_x, mitte_y = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
    spanne_x = max((max(xs) - min(xs)) * 1.3, (max(ys) - min(ys)) * 1.3 * breite / hoehe, math.radians(1.5))
    massstab = breite / spanne_x
    x0, y1 = mitte_x - spanne_x / 2, mitte_y + hoehe / massstab / 2

    def px(lat: float, lon: float) -> tuple[float, float]:
        x, y = _merc(lat, lon)
        return (x - x0) * massstab, (y1 - y) * massstab

    karte = Image.new("RGB", (breite, hoehe), WASSER)
    zeichnen = ImageDraw.Draw(karte)
    rand = 0.3 * breite

    def ringe(sammlung):
        for f in sammlung["features"]:
            g = f["geometry"]
            polygone = g["coordinates"] if g["type"] == "MultiPolygon" else [g["coordinates"]]
            for polygon in polygone:
                for i, ring in enumerate(polygon):
                    pkt = [px(lat, lon) for lon, lat in ring]
                    if (max(p[0] for p in pkt) < -rand or min(p[0] for p in pkt) > breite + rand
                            or max(p[1] for p in pkt) < -rand or min(p[1] for p in pkt) > hoehe + rand):
                        continue
                    yield i, pkt

    gk = _grundkarte()
    for i, pkt in ringe(gk["laender"]):
        if i == 0:
            zeichnen.polygon(pkt, fill=LAND)
    for _, pkt in ringe(gk["regionen"]):
        zeichnen.line(pkt + pkt[:1], fill=REGION, width=2)
    for _, pkt in ringe(gk["laender"]):
        zeichnen.line(pkt + pkt[:1], fill=GRENZE, width=2)

    # Heatmap: weiche Flecken addieren, weichzeichnen, normieren, einfärben
    radius = max(20, round(breite * 0.028))
    fleck = Image.new("L", (2 * radius, 2 * radius), 0)
    fleck.putdata([round(255 * max(0.0, 1 - math.hypot(x - radius, y - radius) / radius) ** 1.5)
                   for y in range(2 * radius) for x in range(2 * radius)])
    waerme = Image.new("L", (breite, hoehe), 0)
    hoechste = max(a for _, _, a in punkte)
    for lat, lon, anzahl in punkte:
        faktor = 0.35 + 0.65 * math.log1p(anzahl) / math.log1p(hoechste)
        x, y = (round(v) for v in px(lat, lon))
        kasten = (x - radius, y - radius, x + radius, y + radius)
        bereich = waerme.crop(kasten)
        waerme.paste(ImageChops.add(bereich, fleck.point(lambda v: round(v * faktor))), kasten)
    waerme = waerme.filter(ImageFilter.GaussianBlur(radius * 0.45))
    hoch = waerme.getextrema()[1]
    if hoch:
        waerme = waerme.point(lambda v: min(255, round(v * 255 / hoch)))
    r, g, b, a = _verlauf()
    farbig = Image.merge("RGBA", [waerme.point(r), waerme.point(g), waerme.point(b), waerme.point(a)])
    karte = karte.convert("RGBA")
    karte.alpha_composite(farbig)

    # Beschriftung: Orte nach Größe (je Ausschnitt), ohne Überschneidungen
    zeichnen = ImageDraw.Draw(karte)
    grad = math.degrees(spanne_x)
    bis = 8 if grad < 3 else 6.5 if grad < 8 else 5 if grad < 20 else 3.5
    schrift, belegt = _schrift(max(18, breite // 70)), []
    for lat, lon, name, ab in sorted(gk["orte"], key=lambda o: o[3]):
        if ab > bis:
            break
        x, y = px(lat, lon)
        if not (10 < x < breite - 10 and 10 < y < hoehe - 10):
            continue
        links, oben, rechts, unten = zeichnen.textbbox((x + 8, y - 12), name, font=schrift)
        kasten = (x - 6, oben - 4, rechts + 4, unten + 4)
        if rechts > breite - 4 or any(not (kasten[2] < k[0] or kasten[0] > k[2] or kasten[3] < k[1] or kasten[1] > k[3])
                                      for k in belegt):
            continue
        belegt.append(kasten)
        zeichnen.ellipse((x - 4, y - 4, x + 4, y + 4), fill=TEXT)
        zeichnen.text((x + 8, y - 12), name, font=schrift, fill=TEXT, stroke_width=3, stroke_fill="#ffffff")
        if len(belegt) >= 40:
            break
    zeichnen.text((breite - 10, hoehe - 8), "Karte: Natural Earth", font=_schrift(max(14, breite // 110)),
                  fill="#6b7079", anchor="rd")

    puffer = io.BytesIO()
    karte.convert("RGB").save(puffer, "PNG", optimize=True)
    return puffer.getvalue()


def data_uri(png: bytes | None) -> str | None:
    return "data:image/png;base64," + base64.b64encode(png).decode() if png else None
