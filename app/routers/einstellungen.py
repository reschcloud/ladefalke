"""Einstellungen: Stichtag, Topliste, Plausibilität, Vorgaben der kWh-Schätzung, Strompreis-Vorbelegung, Reports,
E-Mail, Protokoll und Syslog, Backup."""
import re
import ssl
from decimal import Decimal

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app import auth
from app.db import get_db
from app.formatierung import decimal_lesen, email_lesen, zahl
from app.config import einstellungen as konfiguration
from app.services import app_einstellungen, backup, geheim, mail, syslog
from app.services.app_einstellungen import AUFGABEN_INTERVALLE, BACKUP_ZEITPLAENE, STANDARD, STICHTAGE, TLP, WOCHENTAGE
from app.services.aufloesung import neu_zuordnen
from app.web import meldung, render, weiter

router = APIRouter(prefix="/einstellungen")
GANZZAHLEN = ("leasing_dauer_monate", "leasing_vorwarnung_tage", "fuehrerschein_intervall_monate",
              "fuehrerschein_vorwarnung_tage", "backup_behalten", "protokoll_aufbewahrung_jahre",
              "vignette_vorwarnung_tage", "streckenmaut_vorwarnung_tage")
FEATURES = ("feature_fuehrerschein", "feature_vignette", "feature_streckenmaut")


def _anzeige(e: app_einstellungen.AppEinstellungen) -> dict:
    """Einstellungen als Formularwerte (deutsche Zahlen)."""
    return {
        "stichtag": e.stichtag, "topliste_m": zahl(e.topliste_m, 1) if e.topliste_m is not None else "",
        "topliste_mindestmenge": zahl(e.topliste_mindestmenge, 1),
        "strompreis_vorbelegung": zahl(e.strompreis_vorbelegung, 4),
        "plausibel_ac_max_kw": zahl(e.plausibel_ac_max_kw, 1), "plausibel_dc_min_kw": zahl(e.plausibel_dc_min_kw, 1),
        "plausibel_dc_max_kw": zahl(e.plausibel_dc_max_kw, 0),
        "schaetzung_ac_kw": zahl(e.schaetzung_ac_kw, 1), "schaetzung_dc_kw": zahl(e.schaetzung_dc_kw, 1),
        "schaetzung_obergrenze_kwh": zahl(e.schaetzung_obergrenze_kwh, 1),
        **{feld: str(getattr(e, feld)) for feld in GANZZAHLEN},
        **{feld: getattr(e, feld) for feld in FEATURES},
        "backup_zeitplan": e.backup_zeitplan, "backup_wochentag": str(e.backup_wochentag), "backup_uhrzeit": e.backup_uhrzeit,
        "mail_server": e.mail_server, "mail_port": str(e.mail_port), "mail_verbindung": e.mail_verbindung,
        "mail_benutzer": e.mail_benutzer, "mail_absender": e.mail_absender, "mail_absender_name": e.mail_absender_name,
        "mail_antwort_an": e.mail_antwort_an,  # das Passwort nie ausgeben
        "report_tlp": e.report_tlp, "report_auto": e.report_auto, "report_auto_tag": str(e.report_auto_tag),
        "report_auto_uhrzeit": e.report_auto_uhrzeit, "report_auto_fahrer": e.report_auto_fahrer,
        "report_auto_zusammenfassung": e.report_auto_zusammenfassung, "report_auto_laengere": e.report_auto_laengere,
        "report_auto_karten_cpo": e.report_auto_karten_cpo, "report_auto_flottenbilanz": e.report_auto_flottenbilanz,
        "zuordnungen_auto_an": e.zuordnungen_auto_an, "zuordnungen_auto": e.zuordnungen_auto_intervall,
        "zuordnungen_auto_wochentag": str(e.zuordnungen_auto_wochentag),
        "zuordnungen_auto_tag": str(e.zuordnungen_auto_tag), "zuordnungen_auto_uhrzeit": e.zuordnungen_auto_uhrzeit,
        "aufgaben_auto_an": e.aufgaben_auto_an, "aufgaben_auto": e.aufgaben_auto_intervall,
        "aufgaben_auto_wochentag": str(e.aufgaben_auto_wochentag),
        "aufgaben_auto_tag": str(e.aufgaben_auto_tag), "aufgaben_auto_uhrzeit": e.aufgaben_auto_uhrzeit,
        "aufgaben_auto_nur_offen": e.aufgaben_auto_nur_offen,
        "syslog_server": e.syslog_server, "syslog_port": str(e.syslog_port), "syslog_protokoll": e.syslog_protokoll,
        "syslog_format": e.syslog_format, "syslog_facility": e.syslog_facility, "syslog_hostname": e.syslog_hostname,
        "syslog_aenderungen": e.syslog_aenderungen, "syslog_app": e.syslog_app, "syslog_werte": e.syslog_werte}


def _formular(request: Request, werte: dict, fehler: dict | None = None, status_code: int = 200,
              db: Session | None = None):
    return render(request, "einstellungen.html", {"werte": werte, "fehler": fehler or {},
                                                  "standard": _anzeige(app_einstellungen.aus_werten(STANDARD)),
                                                  "stichtage": list(STICHTAGE.items()),
                                                  "backup": {"liste": backup.liste(), "status": backup.status(),
                                                             "passwort": bool(konfiguration().backup_passwort),
                                                             "zeitplaene": BACKUP_ZEITPLAENE, "wochentage": WOCHENTAGE},
                                                  "tlp": TLP, "aufgaben_intervalle": AUFGABEN_INTERVALLE,
                                                  "monatstage": app_einstellungen.MONATSTAGE,
                                                  "syslog": {"protokolle": syslog.PROTOKOLLE, "formate": syslog.FORMATE,
                                                             "facilities": list(syslog.FACILITIES),
                                                             "status": syslog.status(),
                                                             "rechnername": syslog._hostname(),
                                                             "ca": bool(db and app_einstellungen.laden(db).syslog_ca)},
                                                  "mail": {"verbindungen": mail.VERBINDUNGEN,
                                                           "konfig": mail.konfiguration(db) if db is not None else None,
                                                           "an": request.state.benutzer.email or ""}},
                  status_code)


def _zeitplan(name: str, an: str, intervall: str, wochentag: str, tag: str, uhrzeit: str, daten: dict,
              fehler: dict) -> None:
    """Versandzeitplan prüfen (`<name>_auto_an`, `_auto` = Intervall, `_wochentag`, `_tag` 1–28 oder 0 = Monatsletzter,
    `_uhrzeit`) und in `daten` bzw. `fehler` eintragen."""
    daten[f"{name}_auto_an"] = "1" if an else ""
    if intervall not in AUFGABEN_INTERVALLE:
        fehler[f"{name}_auto"] = "Bitte ein Intervall wählen."
    else:
        daten[f"{name}_auto"] = intervall
    daten[f"{name}_auto_wochentag"] = wochentag if wochentag in [str(i) for i in range(7)] else "0"
    if not tag.strip().isdigit() or not 0 <= int(tag) <= 28:
        fehler[f"{name}_auto_tag"] = "Bitte einen Tag zwischen 1 und 28 oder den Monatsletzten wählen."
    else:
        daten[f"{name}_auto_tag"] = str(int(tag))
    if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", uhrzeit.strip()):
        fehler[f"{name}_auto_uhrzeit"] = "Bitte eine Uhrzeit wie 07:00 angeben."
    else:
        daten[f"{name}_auto_uhrzeit"] = uhrzeit.strip()


@router.get("")
def anzeigen(request: Request, db: Session = Depends(get_db)):
    return _formular(request, _anzeige(app_einstellungen.laden(db)), db=db)


@router.post("", dependencies=[Depends(auth.admin_erforderlich)])  # lesen dürfen alle, ändern nur Admins
def speichern(request: Request, stichtag: str = Form("ende"), topliste_m: str = Form(""),
              topliste_mindestmenge: str = Form(""), strompreis_vorbelegung: str = Form(""),
              plausibel_ac_max_kw: str = Form("12"), plausibel_dc_min_kw: str = Form("10"),
              plausibel_dc_max_kw: str = Form("1000"), schaetzung_ac_kw: str = Form("11"),
              schaetzung_dc_kw: str = Form("90"), schaetzung_obergrenze_kwh: str = Form("50"),
              leasing_dauer_monate: str = Form("36"), leasing_vorwarnung_tage: str = Form("90"),
              fuehrerschein_intervall_monate: str = Form("6"), fuehrerschein_vorwarnung_tage: str = Form("30"),
              backup_zeitplan: str = Form("aus"), backup_wochentag: str = Form("6"), backup_uhrzeit: str = Form("02:00"),
              backup_behalten: str = Form("7"), mail_server: str = Form(""), mail_port: str = Form("587"),
              mail_verbindung: str = Form("starttls"), mail_benutzer: str = Form(""), mail_passwort: str = Form(""),
              mail_passwort_entfernen: str = Form(""), mail_absender: str = Form(""),
              mail_absender_name: str = Form("Ladefalke"), mail_antwort_an: str = Form(""),
              report_tlp: str = Form(""), report_auto: str = Form(""), report_auto_tag: str = Form("5"),
              report_auto_uhrzeit: str = Form("08:00"), report_auto_fahrer: str = Form(""),
              report_auto_zusammenfassung: str = Form(""), report_auto_laengere: str = Form(""),
              protokoll_aufbewahrung_jahre: str = Form("3"), syslog_server: str = Form(""),
              syslog_port: str = Form("514"), syslog_protokoll: str = Form("tcp"), syslog_format: str = Form("5424"),
              syslog_facility: str = Form("local0"), syslog_hostname: str = Form(""), syslog_ca: str = Form(""),
              syslog_ca_entfernen: str = Form(""), syslog_aenderungen: str = Form(""), syslog_app: str = Form(""),
              syslog_werte: str = Form(""), feature_fuehrerschein: str = Form(""), feature_vignette: str = Form(""),
              feature_streckenmaut: str = Form(""), vignette_vorwarnung_tage: str = Form("30"),
              streckenmaut_vorwarnung_tage: str = Form("30"), aufgaben_auto_an: str = Form(""),
              aufgaben_auto: str = Form("woechentlich"),
              aufgaben_auto_wochentag: str = Form("0"), aufgaben_auto_tag: str = Form("1"),
              aufgaben_auto_uhrzeit: str = Form("07:00"), aufgaben_auto_nur_offen: str = Form(""),
              report_auto_karten_cpo: str = Form(""), report_auto_flottenbilanz: str = Form(""),
              zuordnungen_auto_an: str = Form(""), zuordnungen_auto: str = Form("monatlich"),
              zuordnungen_auto_wochentag: str = Form("0"), zuordnungen_auto_tag: str = Form("0"),
              zuordnungen_auto_uhrzeit: str = Form("07:00"),
              gruppe: str = Form(""), db: Session = Depends(get_db)):
    werte = {"stichtag": stichtag, "topliste_m": topliste_m, "topliste_mindestmenge": topliste_mindestmenge,
             "strompreis_vorbelegung": strompreis_vorbelegung, "plausibel_ac_max_kw": plausibel_ac_max_kw,
             "plausibel_dc_min_kw": plausibel_dc_min_kw, "plausibel_dc_max_kw": plausibel_dc_max_kw,
             "schaetzung_ac_kw": schaetzung_ac_kw, "schaetzung_dc_kw": schaetzung_dc_kw,
             "schaetzung_obergrenze_kwh": schaetzung_obergrenze_kwh, "leasing_dauer_monate": leasing_dauer_monate,
             "leasing_vorwarnung_tage": leasing_vorwarnung_tage,
             "fuehrerschein_intervall_monate": fuehrerschein_intervall_monate,
             "fuehrerschein_vorwarnung_tage": fuehrerschein_vorwarnung_tage, "backup_zeitplan": backup_zeitplan,
             "backup_wochentag": backup_wochentag, "backup_uhrzeit": backup_uhrzeit, "backup_behalten": backup_behalten,
             "mail_server": mail_server.strip(), "mail_port": mail_port.strip(), "mail_verbindung": mail_verbindung,
             "mail_benutzer": mail_benutzer.strip(), "mail_absender": mail_absender.strip(),
             "mail_absender_name": mail_absender_name.strip(), "mail_antwort_an": mail_antwort_an.strip(),
             "protokoll_aufbewahrung_jahre": protokoll_aufbewahrung_jahre, "syslog_server": syslog_server.strip(),
             "syslog_port": syslog_port.strip(), "syslog_protokoll": syslog_protokoll, "syslog_format": syslog_format,
             "syslog_facility": syslog_facility, "syslog_hostname": syslog_hostname.strip(),
             "syslog_aenderungen": bool(syslog_aenderungen), "syslog_app": bool(syslog_app),
             "syslog_werte": bool(syslog_werte), "vignette_vorwarnung_tage": vignette_vorwarnung_tage,
             "streckenmaut_vorwarnung_tage": streckenmaut_vorwarnung_tage,
             "feature_fuehrerschein": bool(feature_fuehrerschein), "feature_vignette": bool(feature_vignette),
             "feature_streckenmaut": bool(feature_streckenmaut)}
    fehler, daten = {}, {feld: "1" if werte[feld] else "" for feld in FEATURES}
    for feld in GANZZAHLEN:  # Fristen: ganze Monate bzw. Tage
        text = werte[feld].strip()
        if not text.isdigit():
            fehler[feld] = "Bitte eine ganze Zahl angeben."
        elif int(text) == 0 and feld in ("leasing_dauer_monate", "fuehrerschein_intervall_monate", "backup_behalten",
                                         "protokoll_aufbewahrung_jahre"):
            fehler[feld] = "Bitte einen Wert größer 0 angeben."
        else:
            daten[feld] = str(int(text))
    if backup_zeitplan not in BACKUP_ZEITPLAENE:
        fehler["backup_zeitplan"] = "Bitte einen Zeitplan wählen."
    else:
        daten["backup_zeitplan"] = backup_zeitplan
    if not backup_wochentag.isdigit() or int(backup_wochentag) > 6:
        fehler["backup_wochentag"] = "Bitte einen Wochentag wählen."
    else:
        daten["backup_wochentag"] = backup_wochentag
    if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", backup_uhrzeit.strip()):
        fehler["backup_uhrzeit"] = "Bitte eine Uhrzeit wie 02:00 angeben."
    else:
        daten["backup_uhrzeit"] = backup_uhrzeit.strip()
    # E-Mail: Server, Port, Verbindung, Adressen; das Passwort nur bei Eingabe ändern (leer = behalten)
    daten["mail_server"], daten["mail_benutzer"] = werte["mail_server"], werte["mail_benutzer"]
    daten["mail_absender_name"] = werte["mail_absender_name"]
    if werte["mail_server"] and not re.fullmatch(r"[A-Za-z0-9.-]+", werte["mail_server"]):
        fehler["mail_server"] = "Bitte nur den Servernamen angeben, z. B. smtp.example.com."
    if not werte["mail_port"].isdigit() or not 1 <= int(werte["mail_port"]) <= 65535:
        fehler["mail_port"] = "Bitte einen Port zwischen 1 und 65535 angeben."
    else:
        daten["mail_port"] = str(int(werte["mail_port"]))
    if mail_verbindung not in mail.VERBINDUNGEN:
        fehler["mail_verbindung"] = "Bitte eine Verbindungsart wählen."
    else:
        daten["mail_verbindung"] = mail_verbindung
    for feld in ("mail_absender", "mail_antwort_an"):
        try:
            daten[feld] = email_lesen(werte[feld]) or ""
        except ValueError as e:
            fehler[feld] = str(e)
    daten.update(report_auto="1" if report_auto else "", report_auto_fahrer="1" if report_auto_fahrer else "",
                 report_auto_zusammenfassung="1" if report_auto_zusammenfassung else "",
                 report_auto_karten_cpo="1" if report_auto_karten_cpo else "",
                 report_auto_flottenbilanz="1" if report_auto_flottenbilanz else "",
                 report_auto_laengere="1" if report_auto_laengere else "")
    if not report_auto_tag.strip().isdigit() or not 1 <= int(report_auto_tag) <= 28:
        fehler["report_auto_tag"] = "Bitte einen Tag zwischen 1 und 28 angeben."
    else:
        daten["report_auto_tag"] = str(int(report_auto_tag))
    if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", report_auto_uhrzeit.strip()):
        fehler["report_auto_uhrzeit"] = "Bitte eine Uhrzeit wie 08:00 angeben."
    else:
        daten["report_auto_uhrzeit"] = report_auto_uhrzeit.strip()
    # Offene Aufgaben und Zuordnungen am Stichtag: Schalter und Zeitplan
    _zeitplan("aufgaben", aufgaben_auto_an, aufgaben_auto, aufgaben_auto_wochentag, aufgaben_auto_tag,
              aufgaben_auto_uhrzeit, daten, fehler)
    daten["aufgaben_auto_nur_offen"] = "1" if aufgaben_auto_nur_offen else ""
    _zeitplan("zuordnungen", zuordnungen_auto_an, zuordnungen_auto, zuordnungen_auto_wochentag, zuordnungen_auto_tag,
              zuordnungen_auto_uhrzeit, daten, fehler)
    if report_tlp and report_tlp not in TLP:
        fehler["report_tlp"] = "Bitte eine TLP-Stufe wählen."
    else:
        daten["report_tlp"] = report_tlp
    # Syslog: Server (leer = aus), Port, Transport, Format, Facility, Hostname, CA (PEM, nur bei Eingabe ändern)
    daten.update(syslog_server=werte["syslog_server"], syslog_hostname=werte["syslog_hostname"],
                 syslog_aenderungen="1" if syslog_aenderungen else "", syslog_app="1" if syslog_app else "",
                 syslog_werte="1" if syslog_werte else "")
    if werte["syslog_server"] and not re.fullmatch(r"[A-Za-z0-9.:-]+", werte["syslog_server"]):
        fehler["syslog_server"] = "Bitte nur den Servernamen oder die IP-Adresse angeben, z. B. syslog.example.com."
    if not werte["syslog_port"].isdigit() or not 1 <= int(werte["syslog_port"]) <= 65535:
        fehler["syslog_port"] = "Bitte einen Port zwischen 1 und 65535 angeben."
    else:
        daten["syslog_port"] = str(int(werte["syslog_port"]))
    for feld, erlaubt, text in (("syslog_protokoll", syslog.PROTOKOLLE, "eine Verbindungsart"),
                                ("syslog_format", syslog.FORMATE, "ein Format"),
                                ("syslog_facility", syslog.FACILITIES, "eine Facility")):
        if werte[feld] not in erlaubt:
            fehler[feld] = f"Bitte {text} wählen."
        else:
            daten[feld] = werte[feld]
    if werte["syslog_hostname"] and not re.fullmatch(r"[A-Za-z0-9._-]{1,255}", werte["syslog_hostname"]):
        fehler["syslog_hostname"] = "Bitte nur Buchstaben, Ziffern, Punkt, Binde- und Unterstrich."
    if syslog_ca_entfernen:
        daten["syslog_ca"] = ""
    elif syslog_ca.strip():
        try:
            if "BEGIN CERTIFICATE" not in syslog_ca:
                raise ValueError
            ssl.create_default_context(cadata=syslog_ca.strip())
        except (ValueError, ssl.SSLError):
            fehler["syslog_ca"] = "Bitte ein CA-Zertifikat im PEM-Format einfügen (-----BEGIN CERTIFICATE----- …)."
        else:
            daten["syslog_ca"] = syslog_ca.strip()
    if mail_passwort_entfernen:
        daten["mail_passwort"] = ""
    elif mail_passwort:
        daten["mail_passwort"] = geheim.verschluesseln(mail_passwort)
    if stichtag not in STICHTAGE:
        fehler["stichtag"] = "Bitte einen Stichtag wählen."
    else:
        daten["stichtag"] = stichtag
    for feld, pflicht in (("topliste_m", False), ("topliste_mindestmenge", True), ("strompreis_vorbelegung", True),
                          ("plausibel_ac_max_kw", True), ("plausibel_dc_min_kw", True), ("plausibel_dc_max_kw", True),
                          ("schaetzung_ac_kw", True), ("schaetzung_dc_kw", True), ("schaetzung_obergrenze_kwh", True)):
        try:
            wert = decimal_lesen(werte[feld])
        except ValueError as e:
            fehler[feld] = str(e)
            continue
        if wert is None:
            if pflicht:
                fehler[feld] = "Bitte einen Wert angeben."
            else:
                daten[feld] = ""
        elif wert < 0:
            fehler[feld] = "Der Wert darf nicht negativ sein."
        elif wert == 0 and feld.startswith("schaetzung_"):
            fehler[feld] = "Bitte einen Wert größer 0 angeben."
        else:
            daten[feld] = str(wert)
    if "plausibel_dc_min_kw" in daten and "plausibel_dc_max_kw" in daten and \
            Decimal(daten["plausibel_dc_min_kw"]) >= Decimal(daten["plausibel_dc_max_kw"]):
        fehler["plausibel_dc_max_kw"] = "Die Obergrenze für DC muss über der Untergrenze liegen."
    if fehler:
        return _formular(request, werte, fehler, 422, db=db)
    vorher = app_einstellungen.laden(db).stichtag
    app_einstellungen.speichern(db, daten)
    text = "Einstellungen gespeichert."
    if daten.get("stichtag") != vorher:  # Fahrzeug je Vorgang am neuen Stichtag ermitteln (manuelle bleiben)
        geaendert = neu_zuordnen(db)
        text += f" Stichtag jetzt {STICHTAGE[daten['stichtag']]}: {geaendert} Ladevorgänge neu zugeordnet."
    db.commit()
    syslog.laden(db)
    meldung(request, text)
    # zurück zur zuletzt bearbeiteten Gruppe (Anker aus dem Formular, nur Kleinbuchstaben)
    return weiter("/einstellungen" + (f"#{gruppe}" if re.fullmatch(r"[a-z]{1,30}", gruppe) else ""))



# ------------------------------------------------------------------ Backup (nur Admin; Zeitplan über „Speichern“)

@router.post("/backup/erstellen", dependencies=[Depends(auth.admin_erforderlich)])
def backup_erstellen(request: Request, db: Session = Depends(get_db)):
    try:
        datei = backup.erstellen(db, "manuell")
    except backup.BackupFehler as e:
        meldung(request, f"Sicherung fehlgeschlagen: {e}", "fehler")
    else:
        backup.protokollieren(db, "erstellt", datei.name, {"Art": [None, "manuell"]})
        db.commit()
        meldung(request, f"Sicherung {datei.name} erstellt ({datei.stat().st_size / 1048576:.1f} MB"
                         f"{', verschlüsselt' if datei.name.endswith('.enc') else ', unverschlüsselt'}).")
    return weiter("/einstellungen#backup")


@router.get("/backup/{name}/datei", dependencies=[Depends(auth.admin_erforderlich)])
def backup_herunterladen(name: str):
    s = backup.finden(name)
    if s is None:
        raise HTTPException(404, "Sicherung nicht gefunden")
    return FileResponse(s.datei, filename=s.datei.name, media_type="application/octet-stream")


@router.post("/backup/{name}/loeschen", dependencies=[Depends(auth.admin_erforderlich)])
def backup_loeschen(request: Request, name: str, db: Session = Depends(get_db)):
    if backup.loeschen(name):
        backup.protokollieren(db, "geloescht", name)
        db.commit()
        meldung(request, f"Sicherung {name} gelöscht.")
    else:
        meldung(request, "Sicherung nicht gefunden.", "fehler")
    return weiter("/einstellungen#backup")


# ------------------------------------------------------------------ E-Mail: Testmail (nur Admin)

@router.post("/mail/test", dependencies=[Depends(auth.admin_erforderlich)])
def mail_test(request: Request, an: str = Form(""), db: Session = Depends(get_db)):
    try:
        adresse = email_lesen(an)
    except ValueError as e:
        meldung(request, f"Testmail: {e}", "fehler")
        return weiter("/einstellungen#email")
    if not adresse:
        meldung(request, "Testmail: bitte eine Empfängeradresse angeben.", "fehler")
        return weiter("/einstellungen#email")
    try:
        mail.senden(db, adresse, "Ladefalke – Testmail",
                    "Diese Testmail bestätigt, dass der Mail-Versand von Ladefalke funktioniert.\n\n"
                    f"Gesendet von {request.state.benutzer.name} über die Einstellungen.")
    except mail.MailFehler as e:
        meldung(request, f"Testmail fehlgeschlagen: {e}", "fehler")
    else:
        meldung(request, f"Testmail an {adresse} gesendet.")
    return weiter("/einstellungen#email")


# ------------------------------------------------------------------ Syslog: Testmeldung (nur Admin)

@router.post("/syslog/test", dependencies=[Depends(auth.admin_erforderlich)])
def syslog_test(request: Request, db: Session = Depends(get_db)):
    k = syslog.konfiguration(app_einstellungen.laden(db))
    try:
        syslog.testen(k, request.state.benutzer.name)
    except syslog.SyslogFehler as e:
        meldung(request, f"Syslog-Testmeldung fehlgeschlagen: {e}", "fehler")
    else:
        meldung(request, f"Syslog-Testmeldung an {k.server}:{k.port} ({syslog.PROTOKOLLE[k.protokoll]}) gesendet"
                         + (" – bei UDP ohne Empfangsbestätigung, bitte im Log des Servers nachsehen." if k.protokoll == "udp" else "."))
    return weiter("/einstellungen#protokoll")
