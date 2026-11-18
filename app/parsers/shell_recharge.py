"""Parser: Shell Recharge (Shell EV Charging Solutions B.V., PDF-Rechnung).

Seite 1: Rechnungsnummer, Rechnungsdatum („5-4-2025“), Summe netto („Zwischensumme EUR (netto)“).
Ab „Rechnungsdetails“ je Karte ein Abschnitt: Überschrift „Öffentliches Laden AT-TNM-…“ (eMA-ID = Vertragsnummer),
darunter „UID: …“ und die Tabelle Ladevorgang | Ladestation | Adresse | kWh | Einheit | Menge | Tarif | Gesamtpreis.
- Ladezeile: „30-03-25 09.26 AT*IOY*E… (Ionity) Adresse … 6.286 kWh 6.286 0.5500 3.46“; Tarif je kWh oder je Minute
  („min“), die kWh stehen immer dabei. Ladestation und Adresse können in die nächste Zeile umbrechen – zugeordnet wird
  nach der Spaltenposition (Wortkoordinaten), nicht nach dem Text.
- „Tr. kosten“ (Transaktionskosten) und andere Zeilen ohne kWh gehören zum vorigen Ladevorgang: Betrag zu dessen
  Kosten, Zeitstempel = Ladeende (bei Minutentarif sonst Start + Minuten). Die Aufteilung steht in den Rohdaten.
- Adresse ohne PLZ und Komma („Straße Nr Ort“): als „Straße Nr, Ort“ gespeichert, damit der Ort verortet wird.
- Zahlen mit Dezimalpunkt; Beträge netto; die Rechnung nennt keine Stromart (bleibt offen, Vorgabe des MSP greift).
"""
import re
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path

from app.models import KennungTyp
from app.parsers.basis import Ergebnis, Gebuehr, Parser, ParserFehler, Rechnung, Vorgang, ist_pdf, kwh_runden, pdf_text

RE_DATUM = re.compile(r"^\d{2}-\d{2}-\d{2}$")
RE_ZEIT = re.compile(r"^\d{2}\.\d{2}$")
EINHEITEN = {"kwh", "min"}
ZEILEN_TOLERANZ = 2.5  # pt: Wörter mit fast gleicher Höhe bilden eine Zeile


def _zahl(text: str) -> Decimal:
    try:
        return Decimal(text.replace(",", ""))
    except InvalidOperation:
        raise ValueError(text) from None


def _ist_zahl(text: str) -> bool:
    try:
        _zahl(text)
        return True
    except ValueError:
        return False


def woerter(pfad: Path) -> list[list[dict]]:
    """Wörter je Seite mit Position (x0, x1, top, text)."""
    import pdfplumber
    with pdfplumber.open(pfad) as pdf:
        return [[{"x0": w["x0"], "x1": w["x1"], "top": w["top"], "text": w["text"]} for w in s.extract_words()]
                for s in pdf.pages]


def _zeilen(seite: list[dict]) -> list[list[dict]]:
    """Wörter einer Seite zu Zeilen (von oben nach unten, je Zeile von links nach rechts)."""
    zeilen: list[list[dict]] = []
    for w in sorted(seite, key=lambda w: (w["top"], w["x0"])):
        if zeilen and abs(zeilen[-1][0]["top"] - w["top"]) <= ZEILEN_TOLERANZ:
            zeilen[-1].append(w)
        else:
            zeilen.append([w])
    return [sorted(z, key=lambda w: w["x0"]) for z in zeilen]


class ShellRechargeParser(Parser):
    key = "shell_recharge"
    name = "Shell Recharge (PDF)"
    msp_name = "Shell Recharge"

    def erkennen(self, pfad: Path) -> bool:
        if not ist_pdf(pfad):
            return False
        text = pdf_text(pfad, nur_erste_seite=True)
        return "Shell EV Charging Solutions" in text and "Rechnung" in text

    def parsen(self, pfad: Path) -> Ergebnis:
        return parsen_daten(pdf_text(pfad), woerter(pfad))


def _kopf(text: str) -> Rechnung:
    r = Rechnung()
    if m := re.search(r"Rechnungsnr\.\s*(\S+)", text):
        r.nummer = m.group(1)
    if m := re.search(r"Rechnungsdatum\s+(\d{1,2})-(\d{1,2})-(\d{4})", text):
        r.datum = date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
    if m := re.search(r"Zwischensumme EUR \(netto\)\s+([\d.,]+)", text):
        r.summe_netto = _zahl(m.group(1))
    return r


def parsen_daten(text: str, seiten: list[list[dict]]) -> Ergebnis:
    """`text`: Text des Dokuments (Kopfangaben); `seiten`: Wörter mit Position je Seite (siehe `woerter`)."""
    ergebnis = Ergebnis(rechnung=_kopf(text))
    warnungen = ergebnis.warnungen
    if ergebnis.rechnung.summe_netto is None:
        warnungen.append("„Zwischensumme EUR (netto)“ nicht gefunden – das PDF wirkt unvollständig.")

    in_details, spalten = False, None
    karte, kategorie, uid = None, None, None
    vorige_zeile: list[dict] | None = None
    letzter: Vorgang | None = None  # für Folgezeilen und Zusatzkosten
    karten_summen: dict[str, tuple[Decimal, Decimal]] = {}  # Karte → (kWh, Betrag) laut „Zwischensumme“

    for seite in seiten:
        for zeile in _zeilen(seite):
            texte = [w["text"] for w in zeile]
            if "Rechnungsdetails" in texte:
                in_details = True
                continue
            if not in_details:
                continue
            if texte[0].startswith("UID:"):  # Überschrift der Karte steht in der Zeile davor
                if vorige_zeile:
                    karte = vorige_zeile[-1]["text"]
                    kategorie = " ".join(w["text"] for w in vorige_zeile[:-1]) or None
                uid = " ".join(texte)[4:].strip() or None
                letzter = None
            elif "Ladestation" in texte and "Gesamtpreis" in texte:
                spalten = {w["text"]: w for w in zeile}
            elif texte[0] == "Zwischensumme" and karte and len(texte) >= 5:
                try:
                    karten_summen[karte] = (_zahl(texte[2]), _zahl(texte[-1]))
                except ValueError:
                    pass
            elif spalten and RE_DATUM.match(texte[0]) and len(texte) > 1 and RE_ZEIT.match(texte[1]):
                letzter = _zeile(zeile, spalten, karte, kategorie, uid, letzter, ergebnis) or letzter
            elif spalten and letzter is not None and zeile[0]["x0"] >= spalten["Ladestation"]["x0"] - 2 \
                    and zeile[-1]["x1"] <= spalten["kWh"]["x0"] + 1:
                _folgezeile(zeile, spalten, letzter)
            vorige_zeile = zeile

    for v in ergebnis.vorgaenge:
        _abschliessen(v)
    if not ergebnis.vorgaenge and not ergebnis.gebuehren:
        raise ParserFehler("Keine Ladevorgänge gefunden – ist das eine Shell-Recharge-Rechnung?")

    # Prüfsummen: je Karte und gesamt
    for k, (kwh, betrag) in karten_summen.items():
        eigene = [v for v in ergebnis.vorgaenge if v.kartenkennung == k]
        if sum((v.kwh for v in eigene), Decimal(0)) != kwh_runden(kwh):
            warnungen.append(f"Karte {k}: Summe der kWh weicht von der Zwischensumme {kwh} ab.")
        geb = sum((g.betrag_netto for g in ergebnis.gebuehren if g.kartenkennung == k), Decimal(0))
        if sum((v.kosten_netto for v in eigene), Decimal(0)) + geb != betrag:
            warnungen.append(f"Karte {k}: Summe der Beträge weicht von der Zwischensumme {betrag} EUR ab.")
    summe = sum((v.kosten_netto for v in ergebnis.vorgaenge), Decimal(0)) + \
        sum((g.betrag_netto for g in ergebnis.gebuehren), Decimal(0))
    if ergebnis.rechnung.summe_netto is not None and summe != ergebnis.rechnung.summe_netto:
        warnungen.append(f"Summe der Positionen {summe} EUR ≠ Rechnungssumme netto {ergebnis.rechnung.summe_netto} EUR.")
    if ergebnis.vorgaenge:
        ergebnis.rechnung.zeitraum_von = min(v.start for v in ergebnis.vorgaenge).date()
        ergebnis.rechnung.zeitraum_bis = max(v.ende for v in ergebnis.vorgaenge).date()
    return ergebnis


def _zeile(zeile, spalten, karte, kategorie, uid, letzter, ergebnis) -> Vorgang | None:
    """Tabellenzeile mit Datum: Ladevorgang (mit kWh und Einheit) oder Zusatzkosten zum vorigen Vorgang."""
    texte = [w["text"] for w in zeile]
    zeit = datetime.strptime(f"{texte[0]} {texte[1]}", "%d-%m-%y %H.%M")
    rest = zeile[2:]
    # Ladevorgang: von rechts Gesamtpreis, Tarif, Menge, Einheit, kWh
    if len(rest) >= 5 and rest[-4]["text"].lower() in EINHEITEN and all(_ist_zahl(w["text"]) for w in
                                                                      (rest[-5], rest[-3], rest[-2], rest[-1])):
        kwh, einheit, menge, tarif, betrag = (rest[-5]["text"], rest[-4]["text"], rest[-3]["text"], rest[-2]["text"],
                                              rest[-1]["text"])
        mitte = rest[:-5]
        grenze = spalten["Adresse"]["x0"] - 2
        station = [w["text"] for w in mitte if w["x0"] < grenze]
        adresse = [w["text"] for w in mitte if w["x0"] >= grenze]
        v = Vorgang(kartenkennung=karte, kennung_typ=KennungTyp.vertragsnummer, start=zeit, ende=zeit,
                    kwh=kwh_runden(_zahl(kwh)), kosten_netto=_zahl(betrag),
                    kartenbezeichnung=f"Shell Recharge {karte}" if karte else None,
                    rohdaten={"station": station, "adresse": adresse, "einheit": einheit, "menge": menge,
                              "tarif": tarif, "betrag_laden": betrag, "zusatz": [], "karte_kategorie": kategorie,
                              "uid": uid})
        if not karte:
            ergebnis.warnungen.append(f"Ladevorgang {zeit:%d.%m.%Y %H:%M}: keine Karte zugeordnet.")
        ergebnis.vorgaenge.append(v)
        return v
    # Zusatzkosten (z. B. „Tr. kosten“): Menge, Tarif, Gesamtpreis rechts
    if len(rest) >= 3 and all(_ist_zahl(w["text"]) for w in rest[-3:]):
        bezeichnung = " ".join(w["text"] for w in rest[:-3]) or "Zusatzkosten"
        menge, tarif, betrag = (w["text"] for w in rest[-3:])
        if letzter is not None and letzter.kartenkennung == karte:
            letzter.rohdaten["zusatz"].append({"bezeichnung": bezeichnung, "zeit": zeit.isoformat(), "menge": menge,
                                               "tarif": tarif, "betrag": betrag})
            letzter.kosten_netto += _zahl(betrag)
            if zeit > letzter.start:
                letzter.ende = zeit
        else:
            ergebnis.gebuehren.append(Gebuehr(bezeichnung, _zahl(betrag), kartenkennung=karte,
                                              kennung_typ=KennungTyp.vertragsnummer,
                                              kartenbezeichnung=f"Shell Recharge {karte}" if karte else None,
                                              zeitraum_von=zeit.date(), zeitraum_bis=zeit.date()))
        return None
    ergebnis.warnungen.append(f"Zeile „{' '.join(texte)}“ nicht erkannt.")
    return None


def _folgezeile(zeile, spalten, v: Vorgang) -> None:
    """Umbruch von Ladestation bzw. Adresse in die nächste Zeile."""
    grenze = spalten["Adresse"]["x0"] - 2
    v.rohdaten["station"] += [w["text"] for w in zeile if w["x0"] < grenze]
    v.rohdaten["adresse"] += [w["text"] for w in zeile if w["x0"] >= grenze]


def _adresse(text: str | None) -> str | None:
    """„Musterstraße 5-1 Teststadt“ → „Musterstraße 5-1, Teststadt“: Die Rechnung nennt weder PLZ noch Komma; hinter der
    letzten Hausnummer steht der Ort – abgetrennt findet ihn die Verortung über den Ortsnamen (services/ladeorte.py)."""
    if not text:
        return None
    woerter = text.split()
    nummern = [i for i, w in enumerate(woerter) if re.search(r"\d", w)]
    if not nummern or nummern[-1] >= len(woerter) - 1:
        return text
    i = nummern[-1]
    return f"{' '.join(woerter[:i + 1])}, {' '.join(woerter[i + 1:])}"


def _abschliessen(v: Vorgang) -> None:
    """EVSE-ID (umbrochene Teile ohne Leerzeichen) und Betreiber „(…)“ trennen, Adresse, Ende ohne Zeitstempel."""
    station = v.rohdaten.pop("station")
    evse, betreiber = [], []
    for wort in station:
        (betreiber if betreiber or wort.startswith("(") else evse).append(wort)
    v.ladepunkt = "".join(evse)[:100] or None
    v.rohdaten["evse"] = v.ladepunkt
    v.rohdaten["betreiber_name"] = " ".join(betreiber).strip("() ") or None
    v.rohdaten["adresse"] = " ".join(v.rohdaten["adresse"]) or None
    v.standort = _adresse(v.rohdaten["adresse"])
    if v.ende == v.start and v.rohdaten["einheit"].lower() == "min":
        v.ende = v.start + timedelta(minutes=float(_zahl(v.rohdaten["menge"])))
    v.ende = v.ende.replace(microsecond=0)
