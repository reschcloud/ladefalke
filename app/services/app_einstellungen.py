"""Änderbare Einstellungen (Seite „Einstellungen“). Nicht gesetzte Werte haben Standardwerte."""
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Einstellung

STICHTAGE = {"ende": "Ladeende", "beginn": "Ladebeginn"}

STANDARD = {
    "stichtag": "beginn",  # fest: ein Ladevorgang zählt nach seinem Ladebeginn (früher wählbar, siehe stichtag_festlegen)
    "topliste_m": "",  # leer = Median der Fahrzeug-kWh im Zeitraum
    "topliste_mindestmenge": "20",
    # Kosten und Umsatzsteuer (services/kosten.py): Land des Unternehmens (inländische USt.), Vorsteuerabzug in Prozent
    # für Ladungen und Gebühren ohne Fahrzeug (Gastkarten, nicht zugeordnet)
    "firmenland": "AT",
    "vorsteuer_standard": "100",
    "ust_nachgetragen": "",  # Datum, an dem die USt. aus den Originalrechnungen nachgetragen wurde (ust_nachtrag)
    "fristen_mail_geprueft": "",  # Tag der letzten Prüfung für Frist-Benachrichtigungen (services/fristen_mail.py)
    # Vergleich mit Verbrennern (grobe Schätzungen): Verbrauch E-PKW kWh/100 km und Verbrenner l/100 km (für beide);
    # CO₂e-Einsparung: Schalter, Strommix g CO₂e/kWh, Emission Verbrenner kg CO₂e/l; gesparte Tankkosten: Schalter
    # (Spritpreise mit „gültig ab“ in der Tabelle spritpreis)
    "co2e_verbrauch_e": "20",
    "co2e_verbrauch_verbrenner": "7.4",
    "feature_co2e": "",
    "co2e_strom": "",
    "co2e_verbrenner": "3.2",
    "feature_tank": "",
    "sprit_art": "diesel",  # Kraftstoff des Vergleichs: diesel | euro95 (Oil Bulletin)
    "oil_bulletin_auto": "1",  # Spritpreise wöchentlich aus dem EU Weekly Oil Bulletin laden (nur mit feature_tank)
    "oil_bulletin_status": "",  # JSON: letzter Abruf (services/oil_bulletin.py)
    # System: GeoNames-Postleitzahlen selbst laden und aktuell halten (services/geodaten.py), täglich nach neuen
    # Releases sehen (services/aktualisierung.py); dazu jeweils der letzte Stand als JSON
    "geodaten_auto": "1",
    "geodaten_status": "",
    "update_pruefen": "1",
    "update_status": "",
    # Plausibilität AC/DC (Leistung = kWh / angesteckte Dauer)
    "plausibel_ac_max_kw": "12",  # AC ab diesem Wert unplausibel
    "plausibel_dc_min_kw": "10",  # DC unter diesem Wert unplausibel
    "plausibel_dc_max_kw": "1000",  # DC ab diesem Wert unplausibel
    # Vorgabewerte der kWh-Schätzung bei Zeittarifen, solange es zu wenige gemessene Vorgänge gibt
    "schaetzung_ac_kw": "11",
    "schaetzung_dc_kw": "90",
    "schaetzung_obergrenze_kwh": "50",
    # Fristen (Aufgaben): Leasing und Führerscheinkontrolle
    "leasing_dauer_monate": "36",  # Vorbelegung des Leasing-Endes aus dem Beginn
    "leasing_vorwarnung_tage": "90",
    "fuehrerschein_intervall_monate": "6",
    "fuehrerschein_vorwarnung_tage": "30",
    # Features je Installation (Einstellungen → Fristen): leer = aus. Aus = keine Felder, Hinweise oder Aufgaben.
    "feature_fuehrerschein": "",
    "feature_vignette": "",
    "feature_streckenmaut": "",
    "vignette_vorwarnung_tage": "30",
    "streckenmaut_vorwarnung_tage": "30",
    # Ladekarten je Fahrzeug prüfen (services/kartenpruefung.py): an/aus und erlaubte Anzahl gleichzeitiger Karten
    "feature_kartenpruefung": "1",
    "karten_min": "1",
    "karten_max": "1",
    # Backup (services/backup.py): Zeitplan aus | taeglich | woechentlich, Wochentag 0 = Montag
    "backup_zeitplan": "aus",
    "backup_wochentag": "6",
    "backup_uhrzeit": "02:00",
    "backup_behalten": "7",
    # E-Mail (services/mail.py); Passwort verschlüsselt (services/geheim.py)
    "mail_server": "",
    "mail_port": "587",
    "mail_verbindung": "starttls",
    "mail_benutzer": "",
    "mail_passwort": "",
    "mail_absender": "",
    "mail_absender_name": "Ladefalke",
    "mail_antwort_an": "",
    # Reports: TLP-Kennzeichnung (Traffic Light Protocol 2.0) in der Fußzeile der PDFs; leer = keine
    "report_tlp": "",
    # automatischer Report-Versand (services/versand.py): an/aus, Tag des Folgemonats, Uhrzeit, was verschickt wird
    "report_auto": "",
    "report_auto_tag": "5",
    "report_auto_uhrzeit": "08:00",
    "report_auto_fahrer": "1",
    "report_auto_zusammenfassung": "1",
    "report_auto_karten_cpo": "",  # Karten und Preise je CPO – an Benutzer:innen mit Abo
    "report_auto_flottenbilanz": "",  # Flottenbilanz – an Benutzer:innen mit Abo
    "report_auto_laengere": "",
    # Report „Offene Aufgaben“ automatisch (services/versand.py): Schalter und Intervall (taeglich | woechentlich |
    # monatlich; „aus“ aus früheren Ständen gilt als ausgeschaltet)
    "aufgaben_auto_an": "",
    "aufgaben_auto": "woechentlich",
    "aufgaben_auto_wochentag": "0",  # Montag
    "aufgaben_auto_tag": "1",  # 1–28; 0 = Monatsletzter
    "aufgaben_auto_uhrzeit": "07:00",
    "aufgaben_auto_nur_offen": "1",  # nur senden, wenn etwas offen ist
    # Report „Zuordnungen am Stichtag“ automatisch (Stichtag = Versandtag), Zeitplan wie bei den offenen Aufgaben
    "zuordnungen_auto_an": "",
    "zuordnungen_auto": "monatlich",
    "zuordnungen_auto_wochentag": "0",
    "zuordnungen_auto_tag": "0",  # Monatsletzter
    "zuordnungen_auto_uhrzeit": "07:00",
    # Änderungsprotokoll (services/protokoll.py): Aufbewahrung in Jahren
    "protokoll_aufbewahrung_jahre": "3",
    # Syslog (services/syslog.py): leerer Server = aus; CA (PEM) nur für TLS mit eigenem Zertifikat
    "syslog_server": "",
    "syslog_port": "514",
    "syslog_protokoll": "tcp",
    "syslog_format": "5424",
    "syslog_facility": "local0",
    "syslog_hostname": "",
    "syslog_ca": "",
    "syslog_aenderungen": "1",
    "syslog_app": "1",
    "syslog_werte": "1",
}
# TLP 2.0: Stufe → (Bedeutung, Schriftfarbe auf Schwarz laut FIRST)
TLP = {"CLEAR": ("ohne Einschränkung weitergeben", "#ffffff"),
       "GREEN": ("innerhalb der Gemeinschaft, nicht öffentlich", "#33ff00"),
       "AMBER": ("innerhalb der eigenen Organisation und mit deren Kund:innen, soweit nötig", "#ffc000"),
       "AMBER+STRICT": ("nur innerhalb der eigenen Organisation", "#ffc000"),
       "RED": ("nur für die genannten Empfänger:innen", "#ff2b2b")}
BACKUP_ZEITPLAENE = {"aus": "aus", "taeglich": "täglich", "woechentlich": "wöchentlich"}
AUFGABEN_ZEITPLAENE = {"aus": "aus", "taeglich": "täglich", "woechentlich": "wöchentlich", "monatlich": "monatlich"}
AUFGABEN_INTERVALLE = {k: v for k, v in AUFGABEN_ZEITPLAENE.items() if k != "aus"}
MONATSTAGE = [(str(t), f"{t}.") for t in range(1, 29)] + [("0", "Monatsletzten")]  # Auswahl „am …“
WOCHENTAGE = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"]


@dataclass(frozen=True)
class AppEinstellungen:
    stichtag: str  # "ende" | "beginn" – ordnet Ladevorgänge den Auswertungszeiträumen zu
    topliste_m: Decimal | None  # Gewichtungskonstante in kWh; None = Median
    topliste_mindestmenge: Decimal
    firmenland: str = "AT"  # ISO-Code; USt. dieses Landes ist als Vorsteuer abziehbar (je Fahrzeug)
    vorsteuer_standard: Decimal = Decimal(100)  # Prozent, für Posten ohne Fahrzeug
    ust_nachgetragen: str = ""  # ISO-Datum des letzten Nachtrags der USt. (leer = noch nie)
    co2e_verbrauch_e: Decimal = Decimal(20)  # kWh/100 km
    co2e_verbrauch_verbrenner: Decimal = Decimal("7.4")  # l/100 km
    feature_co2e: bool = False  # CO₂e-Einsparung schätzen
    co2e_strom: Decimal | None = None  # g CO₂e/kWh des geladenen Stroms
    co2e_verbrenner: Decimal = Decimal("3.2")  # kg CO₂e/l Kraftstoff
    feature_tank: bool = False  # gesparte Tankkosten schätzen (Spritpreise: Tabelle spritpreis)
    sprit_art: str = "diesel"  # diesel | euro95
    oil_bulletin_auto: bool = True  # Spritpreise automatisch aus dem Oil Bulletin
    geodaten_auto: bool = True  # Postleitzahlen automatisch laden (erster Start, nach Updates, monatlich)
    update_pruefen: bool = True  # täglich nach neuen Releases sehen

    @property
    def co2e_ersparnis_je_kwh(self) -> Decimal | None:
        """Grob geschätzte CO₂e-Einsparung in kg je geladener kWh gegenüber einem Verbrenner für dieselbe Strecke:
        (Verbrenner-l/100 km × kg/l) / (E-kWh/100 km) − Strom-g/kWh / 1000; None, wenn ausgeschaltet."""
        if not self.feature_co2e or self.co2e_strom is None or not self.co2e_verbrauch_e:
            return None
        return (self.co2e_verbrauch_verbrenner * self.co2e_verbrenner / self.co2e_verbrauch_e
                - self.co2e_strom / 1000)

    @property
    def liter_je_kwh(self) -> Decimal | None:
        """Liter, die ein Verbrenner für die Strecke einer geladenen kWh bräuchte (l/100 km ÷ kWh/100 km)."""
        return self.co2e_verbrauch_verbrenner / self.co2e_verbrauch_e if self.co2e_verbrauch_e else None

    plausibel_ac_max_kw: Decimal = Decimal(12)
    plausibel_dc_min_kw: Decimal = Decimal(10)
    plausibel_dc_max_kw: Decimal = Decimal(1000)
    schaetzung_ac_kw: Decimal = Decimal(11)
    schaetzung_dc_kw: Decimal = Decimal(90)
    schaetzung_obergrenze_kwh: Decimal = Decimal(50)
    leasing_dauer_monate: int = 36
    leasing_vorwarnung_tage: int = 90
    fuehrerschein_intervall_monate: int = 6
    fuehrerschein_vorwarnung_tage: int = 30
    feature_fuehrerschein: bool = False  # Führerscheinkontrolle überwachen
    feature_vignette: bool = False  # Autobahn-Vignette (AT) je Fahrzeug
    feature_streckenmaut: bool = False  # Streckenmaut FLEX (AT) je Fahrzeug
    vignette_vorwarnung_tage: int = 30
    streckenmaut_vorwarnung_tage: int = 30
    feature_kartenpruefung: bool = True  # Anzahl der Fahrzeugkarten je Fahrzeug prüfen
    karten_min: int = 1
    karten_max: int = 1
    backup_zeitplan: str = "aus"
    backup_wochentag: int = 6
    backup_uhrzeit: str = "02:00"
    backup_behalten: int = 7
    mail_server: str = ""
    mail_port: int = 587
    mail_verbindung: str = "starttls"
    mail_benutzer: str = ""
    mail_passwort: str = ""  # verschlüsselt (Fernet-Token)
    mail_absender: str = ""
    mail_absender_name: str = "Ladefalke"
    mail_antwort_an: str = ""
    report_tlp: str = ""  # Stufe aus TLP oder leer
    report_auto: bool = False
    report_auto_tag: int = 5
    report_auto_uhrzeit: str = "08:00"
    report_auto_fahrer: bool = True
    report_auto_zusammenfassung: bool = True
    report_auto_karten_cpo: bool = False
    report_auto_flottenbilanz: bool = False
    report_auto_laengere: bool = False  # zusätzlich Quartal/Halbjahr/Jahr, wenn der Vormonat ihn abschließt
    aufgaben_auto: str = "aus"  # wirksam: „aus“ oder das Intervall, wenn eingeschaltet
    aufgaben_auto_an: bool = False
    aufgaben_auto_intervall: str = "woechentlich"
    aufgaben_auto_wochentag: int = 0
    aufgaben_auto_tag: int = 1  # 0 = Monatsletzter
    aufgaben_auto_uhrzeit: str = "07:00"
    aufgaben_auto_nur_offen: bool = True
    zuordnungen_auto: str = "aus"  # wirksam: „aus“ oder das Intervall, wenn eingeschaltet
    zuordnungen_auto_an: bool = False
    zuordnungen_auto_intervall: str = "monatlich"
    zuordnungen_auto_wochentag: int = 0
    zuordnungen_auto_tag: int = 0  # 0 = Monatsletzter
    zuordnungen_auto_uhrzeit: str = "07:00"
    protokoll_aufbewahrung_jahre: int = 3
    syslog_server: str = ""
    syslog_port: int = 514
    syslog_protokoll: str = "tcp"  # udp | tcp | tls
    syslog_format: str = "5424"  # 5424 | 3164
    syslog_facility: str = "local0"
    syslog_hostname: str = ""  # leer = Rechnername
    syslog_ca: str = ""  # PEM, nur TLS
    syslog_aenderungen: bool = True  # Änderungsprotokoll senden
    syslog_app: bool = True  # Meldungen der App (Fehler, Backups, Versand) senden
    syslog_werte: bool = True  # alte und neue Werte mitsenden


def laden(db: Session) -> AppEinstellungen:
    gespeichert = {e.schluessel: e.wert for e in db.scalars(select(Einstellung))}
    # früherer Stand ohne Schalter: ein gespeichertes Intervall (statt „aus“) hieß „eingeschaltet“
    if "aufgaben_auto_an" not in gespeichert and gespeichert.get("aufgaben_auto") in AUFGABEN_INTERVALLE:
        gespeichert["aufgaben_auto_an"] = "1"
    return aus_werten({**STANDARD, **gespeichert})


def _tag(text: str, standard: int) -> int:
    """Versandtag im Monat: 1–28, 0 = Monatsletzter."""
    try:
        tag = int(text)
    except (TypeError, ValueError):
        return standard
    return 0 if tag == 0 else min(28, max(1, tag))


def aus_werten(werte: dict[str, str]) -> AppEinstellungen:
    """Gespeicherte Texte → Einstellungen; `aus_werten(STANDARD)` liefert die Standardwerte."""
    return AppEinstellungen(
        stichtag="beginn",  # fest (seit 11/2026); gespeicherte Werte zählen nicht mehr
        topliste_m=Decimal(werte["topliste_m"]) if werte["topliste_m"] else None,
        topliste_mindestmenge=Decimal(werte["topliste_mindestmenge"]),
        firmenland=(werte["firmenland"] or "AT").upper()[:2],
        ust_nachgetragen=werte["ust_nachgetragen"],
        vorsteuer_standard=min(Decimal(100), max(Decimal(0), Decimal(werte["vorsteuer_standard"] or 100))),
        co2e_strom=Decimal(werte["co2e_strom"]) if werte["co2e_strom"] else None,
        co2e_verbrauch_e=Decimal(werte["co2e_verbrauch_e"] or 20),
        co2e_verbrauch_verbrenner=Decimal(werte["co2e_verbrauch_verbrenner"] or "7.4"),
        co2e_verbrenner=Decimal(werte["co2e_verbrenner"] or "3.2"),
        feature_co2e=bool(werte["feature_co2e"]), feature_tank=bool(werte["feature_tank"]),
        sprit_art=werte["sprit_art"] if werte["sprit_art"] in ("diesel", "euro95") else "diesel",
        oil_bulletin_auto=bool(werte["oil_bulletin_auto"]),
        geodaten_auto=bool(werte["geodaten_auto"]), update_pruefen=bool(werte["update_pruefen"]),
        plausibel_ac_max_kw=Decimal(werte["plausibel_ac_max_kw"]),
        plausibel_dc_min_kw=Decimal(werte["plausibel_dc_min_kw"]),
        plausibel_dc_max_kw=Decimal(werte["plausibel_dc_max_kw"]),
        schaetzung_ac_kw=Decimal(werte["schaetzung_ac_kw"]),
        schaetzung_dc_kw=Decimal(werte["schaetzung_dc_kw"]),
        schaetzung_obergrenze_kwh=Decimal(werte["schaetzung_obergrenze_kwh"]),
        leasing_dauer_monate=int(werte["leasing_dauer_monate"]),
        leasing_vorwarnung_tage=int(werte["leasing_vorwarnung_tage"]),
        fuehrerschein_intervall_monate=int(werte["fuehrerschein_intervall_monate"]),
        fuehrerschein_vorwarnung_tage=int(werte["fuehrerschein_vorwarnung_tage"]),
        feature_fuehrerschein=bool(werte["feature_fuehrerschein"]), feature_vignette=bool(werte["feature_vignette"]),
        feature_streckenmaut=bool(werte["feature_streckenmaut"]),
        vignette_vorwarnung_tage=int(werte["vignette_vorwarnung_tage"] or 30),
        streckenmaut_vorwarnung_tage=int(werte["streckenmaut_vorwarnung_tage"] or 30),
        feature_kartenpruefung=bool(werte["feature_kartenpruefung"]),
        karten_min=int(werte["karten_min"] or 0), karten_max=max(int(werte["karten_min"] or 0), int(werte["karten_max"] or 0)),
        backup_zeitplan=werte["backup_zeitplan"] if werte["backup_zeitplan"] in BACKUP_ZEITPLAENE else "aus",
        backup_wochentag=int(werte["backup_wochentag"]) % 7,
        backup_uhrzeit=werte["backup_uhrzeit"],
        backup_behalten=max(1, int(werte["backup_behalten"])),
        mail_server=werte["mail_server"], mail_port=int(werte["mail_port"] or 587),
        mail_verbindung=werte["mail_verbindung"] if werte["mail_verbindung"] in ("starttls", "ssl", "keine") else "starttls",
        mail_benutzer=werte["mail_benutzer"], mail_passwort=werte["mail_passwort"], mail_absender=werte["mail_absender"],
        mail_absender_name=werte["mail_absender_name"], mail_antwort_an=werte["mail_antwort_an"],
        report_tlp=werte["report_tlp"] if werte["report_tlp"] in TLP else "",
        report_auto=bool(werte["report_auto"]), report_auto_tag=min(28, max(1, int(werte["report_auto_tag"] or 5))),
        report_auto_uhrzeit=werte["report_auto_uhrzeit"] or "08:00", report_auto_fahrer=bool(werte["report_auto_fahrer"]),
        report_auto_zusammenfassung=bool(werte["report_auto_zusammenfassung"]),
        report_auto_karten_cpo=bool(werte["report_auto_karten_cpo"]),
        report_auto_flottenbilanz=bool(werte["report_auto_flottenbilanz"]),
        report_auto_laengere=bool(werte["report_auto_laengere"]),
        aufgaben_auto_an=bool(werte["aufgaben_auto_an"]) and werte["aufgaben_auto"] in AUFGABEN_INTERVALLE,
        aufgaben_auto_intervall=werte["aufgaben_auto"] if werte["aufgaben_auto"] in AUFGABEN_INTERVALLE else "woechentlich",
        aufgaben_auto=(werte["aufgaben_auto"] if werte["aufgaben_auto_an"] and werte["aufgaben_auto"] in AUFGABEN_INTERVALLE
                       else "aus"),
        aufgaben_auto_wochentag=int(werte["aufgaben_auto_wochentag"] or 0) % 7,
        aufgaben_auto_tag=_tag(werte["aufgaben_auto_tag"], 1),
        aufgaben_auto_uhrzeit=werte["aufgaben_auto_uhrzeit"] or "07:00",
        aufgaben_auto_nur_offen=bool(werte["aufgaben_auto_nur_offen"]),
        zuordnungen_auto_an=bool(werte["zuordnungen_auto_an"]) and werte["zuordnungen_auto"] in AUFGABEN_INTERVALLE,
        zuordnungen_auto_intervall=(werte["zuordnungen_auto"] if werte["zuordnungen_auto"] in AUFGABEN_INTERVALLE
                                    else "monatlich"),
        zuordnungen_auto=(werte["zuordnungen_auto"] if werte["zuordnungen_auto_an"]
                          and werte["zuordnungen_auto"] in AUFGABEN_INTERVALLE else "aus"),
        zuordnungen_auto_wochentag=int(werte["zuordnungen_auto_wochentag"] or 0) % 7,
        zuordnungen_auto_tag=_tag(werte["zuordnungen_auto_tag"], 0),
        zuordnungen_auto_uhrzeit=werte["zuordnungen_auto_uhrzeit"] or "07:00",
        protokoll_aufbewahrung_jahre=max(1, int(werte["protokoll_aufbewahrung_jahre"] or 3)),
        syslog_server=werte["syslog_server"], syslog_port=int(werte["syslog_port"] or 514),
        syslog_protokoll=werte["syslog_protokoll"] if werte["syslog_protokoll"] in ("udp", "tcp", "tls") else "tcp",
        syslog_format=werte["syslog_format"] if werte["syslog_format"] in ("5424", "3164") else "5424",
        syslog_facility=werte["syslog_facility"] or "local0", syslog_hostname=werte["syslog_hostname"],
        syslog_ca=werte["syslog_ca"], syslog_aenderungen=bool(werte["syslog_aenderungen"]),
        syslog_app=bool(werte["syslog_app"]), syslog_werte=bool(werte["syslog_werte"]),
    )


def speichern(db: Session, werte: dict[str, str]) -> None:
    """Werte als Text speichern (Zahlen mit Dezimalpunkt); leerer Text = Standardwert bzw. „kein Wert“."""
    with db.no_autoflush:  # in einem Schritt speichern (ein Eintrag im Änderungsprotokoll)
        for schluessel, wert in werte.items():
            if schluessel not in STANDARD:
                raise KeyError(schluessel)
            eintrag = db.get(Einstellung, schluessel)
            if eintrag is None:
                db.add(Einstellung(schluessel=schluessel, wert=wert))
            else:
                eintrag.wert = wert
    db.flush()
