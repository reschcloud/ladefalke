"""Report-Versand: Fahrer:innen-Reports und Zusammenfassung per Mail, Protokoll, Abo, automatischer Versand, Rechte."""
from datetime import datetime

import pytest
from sqlalchemy import select

from app.models import Benutzer, ReportVersand
from app.services import app_einstellungen, geheim, mail, versand
from app.services.auswertung import zeitraum
from tests.test_benutzer import rollen  # noqa: F401  (Fixture)
from tests.test_cpo_personen import daten  # noqa: F401  (Fixture: Anna mit Ladungen 09/2026)
from tests.test_mail import ScheinServer, schein  # noqa: F401  (Fixture)

SEP = "art=monat&jahr=2026&nr=9"


@pytest.fixture
def mailbereit(db):
    app_einstellungen.speichern(db, {"mail_server": "smtp.example.net", "mail_port": "587", "mail_verbindung": "starttls",
                                     "mail_benutzer": "x@example.net", "mail_passwort": geheim.verschluesseln("pw"),
                                     "mail_absender": "ladefalke@example.net", "mail_absender_name": "Fuhrpark"})
    db.flush()


def test_fahrer_report_senden(db, daten, mailbereit, schein):  # noqa: F811
    z = zeitraum("monat", 2026, 9)
    ergebnis = versand.fahrer_senden(db, z)
    assert ergebnis.ohne_adresse == ["Anna CPO"] and not ergebnis.gesendet  # noch ohne Adresse
    daten["anna"].email = "anna@example.com"
    db.flush()
    ergebnis = versand.fahrer_senden(db, z, [daten["anna"].id], von=None)
    assert ergebnis.gesendet == ["Anna CPO"]
    [(_, nachricht)] = schein.gesendet
    assert nachricht["To"] == "anna@example.com" and nachricht["Subject"] == "Dein Ladereport 09/2026"
    text = nachricht.get_body(("plain",)).get_content()
    assert text.startswith("Hallo Anna CPO,") and "anbei dein Ladereport" in text and text.rstrip().endswith("Viele Grüße\nDein Ladefalke")
    [anhang] = list(nachricht.iter_attachments())
    assert anhang.get_filename() == "ladefalke-report-anna-cpo-09-2026.pdf" and anhang.get_content().startswith(b"%PDF")
    [eintrag] = db.scalars(select(ReportVersand)).all()
    assert eintrag.erfolg and eintrag.art == "fahrer" and eintrag.zeitraum_label == "09/2026" and eintrag.ausgeloest == "manuell"


def test_fehler_wird_protokolliert(db, daten, mailbereit, schein):  # noqa: F811
    daten["anna"].email = "anna@example.com"
    db.flush()
    schein.fehler = ConnectionRefusedError("abgelehnt")
    ergebnis = versand.fahrer_senden(db, zeitraum("monat", 2026, 9))
    assert ergebnis.fehler and "nicht erreichbar" in ergebnis.fehler[0][1]
    [eintrag] = db.scalars(select(ReportVersand)).all()
    assert not eintrag.erfolg and "nicht erreichbar" in eintrag.fehler


def test_ohne_mail_einstellungen(db, daten):
    with pytest.raises(mail.MailFehler, match="Kein Mailserver"):
        versand.fahrer_senden(db, zeitraum("monat", 2026, 9))


def test_zusammenfassung_und_abo(rollen, db, daten, mailbereit, schein):  # noqa: F811
    admin, viewer = rollen["admin"], rollen["viewer"]
    viktor = db.scalar(select(Benutzer).where(Benutzer.benutzername == "viktor"))
    admin.post(f"/benutzer/{viktor.id}", data={"name": "Viktor", "rolle": "viewer", "aktiv": "on", "email": "v@example.com"})
    viewer.post("/konto/abo", data={"abo": "on"})  # Abo darf jede:r selbst setzen
    db.refresh(viktor)
    assert viktor.zusammenfassung_abo
    seite = admin.get(f"/reports?{SEP}").text
    eintrag = seite.split(f'name="benutzer_id" value="{viktor.id}"')[1].split("</li>")[0]
    assert "checked>" in eintrag and "· Abo" in eintrag  # Abonnent:innen vorausgewählt
    antwort = admin.post(f"/reports/zusammenfassung/senden?{SEP}", data={"benutzer_id": [str(viktor.id)]})
    assert "Zusammenfassung 09/2026: 1 gesendet" in antwort.text
    [(_, nachricht)] = schein.gesendet
    assert nachricht["Subject"] == "Ladefalke – Zusammenfassung 09/2026" and nachricht["To"] == "v@example.com"
    assert "Versandprotokoll" in admin.get(f"/reports?{SEP}").text
    # Viewer dürfen nichts senden
    assert viewer.post(f"/reports/zusammenfassung/senden?{SEP}", data={"benutzer_id": [str(viktor.id)]}).status_code == 403
    assert viewer.post(f"/reports/fahrer/senden?{SEP}").status_code == 403


def test_reports_seite_knoepfe(angemeldet, db, daten):
    seite = angemeldet.get(f"/reports?{SEP}").text
    assert "Versand per Mail noch nicht möglich" in seite  # ohne Mail-Einstellungen: Hinweis, Knöpfe ausgegraut
    knopf = seite.split('data-nur="%d"' % daten["anna"].id)[1].split("</button>")[0]
    assert 'aria-disabled="true"' in knopf and "Kein Mailserver eingetragen" in knopf


def test_automatischer_versand(db, daten, mailbereit, schein, monkeypatch):  # noqa: F811
    from app.services.app_einstellungen import laden
    daten["anna"].email = "anna@example.com"
    app_einstellungen.speichern(db, {"report_auto": "1", "report_auto_tag": "5", "report_auto_uhrzeit": "08:00",
                                     "report_auto_fahrer": "1", "report_auto_zusammenfassung": "", "report_auto_laengere": "1"})
    db.flush()
    e = laden(db)
    assert not versand.automatisch_faellig(e, datetime(2026, 10, 5, 7, 59))
    assert versand.automatisch_faellig(e, datetime(2026, 10, 5, 8, 0)) and versand.automatisch_faellig(e, datetime(2026, 10, 20, 1))
    assert [z.label for z in versand.faellige_zeitraeume(datetime(2026, 10, 5), True)] == ["09/2026", "Q3 2026"]
    assert [z.label for z in versand.faellige_zeitraeume(datetime(2027, 1, 5), True)] == [
        "12/2026", "Q4 2026", "2. Halbjahr 2026", "2026"]
    # Ablauf wie im Taktgeber, aber in der Test-Sitzung: Vormonat 09/2026, einmalig je Person
    z = zeitraum("monat", 2026, 9)
    assert versand.fahrer_senden(db, z, ausgeloest="automatisch", nur_neue=True).gesendet == ["Anna CPO"]
    assert versand.fahrer_senden(db, z, ausgeloest="automatisch", nur_neue=True).gesendet == []  # nicht doppelt
    assert len(schein.gesendet) == 1


def test_versanddialoge(angemeldet, db, daten, mailbereit):
    """Je Report ein Knopf „Per Mail senden …“ und ein Dialog; Fahrer:innen ohne freie Adressen, mit Kopie an mich."""
    daten["anna"].email = "anna@example.com"
    db.flush()
    seite = angemeldet.get(f"/reports?{SEP}").text
    for report in ("zusammenfassung", "karten_cpo", "flottenbilanz", "aufgaben", "fahrer"):
        assert f'data-dialog="dialog-{report}"' in seite and f'<dialog id="dialog-{report}"' in seite
    fahrer = seite.split('<dialog id="dialog-fahrer"')[1].split("</dialog>")[0]
    assert 'name="adressen"' not in fahrer and 'name="kopie"' in fahrer
    assert f'name="fahrer_id" value="{daten["anna"].id}"\n            checked' in fahrer
    flotte = seite.split('<dialog id="dialog-flottenbilanz"')[1].split("</dialog>")[0]
    assert 'name="bilder"' in flotte and 'name="adressen"' in flotte and "TLP:" not in flotte


def test_mailtext_fahrer():
    from types import SimpleNamespace
    from decimal import Decimal
    betreff, text = versand.fahrer_text(SimpleNamespace(name="Anna"), zeitraum("monat", 2026, 9),
                                        SimpleNamespace(anzahl=5, kwh=Decimal(52), kosten=Decimal("22.2"), preis=Decimal("0.43")),
                                        "Fuhrpark")
    assert betreff == "Dein Ladereport 09/2026"
    assert "Deinen Platz in der Topliste findest du im PDF." in text and "Im PDF findest du die Details" not in text


# ------------------------------------------------------------------ Offene Aufgaben

def test_aufgaben_manuell_und_abo(rollen, db, daten, mailbereit, schein):  # noqa: F811
    admin, viewer = rollen["admin"], rollen["viewer"]
    viktor = db.scalar(select(Benutzer).where(Benutzer.benutzername == "viktor"))
    admin.post(f"/benutzer/{viktor.id}", data={"name": "Viktor", "rolle": "viewer", "aktiv": "on", "email": "v@example.com"})
    viewer.post("/konto/aufgaben-abo", data={"abo": "on"})  # auch Viewer dürfen abonnieren
    db.refresh(viktor)
    assert viktor.aufgaben_abo and not viktor.zusammenfassung_abo
    seite = admin.get(f"/reports?{SEP}").text
    eintrag = seite.split('id="dialog-aufgaben"')[1].split(f'value="{viktor.id}"')[1].split("</li>")[0]
    assert "checked" in eintrag and "· Abo" in eintrag
    antwort = admin.post(f"/reports/aufgaben/senden?{SEP}", data={"benutzer_id": [str(viktor.id)]})
    assert "Offene Aufgaben: 1 gesendet" in antwort.text
    [(_, nachricht)] = schein.gesendet
    assert nachricht["Subject"].startswith("Ladefalke – Offene Aufgaben (Stand ")
    text = nachricht.get_body(("plain",)).get_content()
    assert "Offen sind:" in text and "Ladevorgänge ohne AC/DC-Angabe" in text
    [anhang] = list(nachricht.iter_attachments())
    assert anhang.get_filename().startswith("ladefalke-offene-aufgaben-") and anhang.get_content().startswith(b"%PDF")
    [eintrag] = db.scalars(select(ReportVersand).where(ReportVersand.art == "aufgaben")).all()
    assert eintrag.erfolg and eintrag.zeitraum_label.startswith("Stand ") and eintrag.ausgeloest == "manuell"
    assert "Offene Aufgaben Stand" in admin.get(f"/reports?{SEP}").text  # Versandprotokoll
    assert viewer.post(f"/reports/aufgaben/senden?{SEP}", data={"benutzer_id": [str(viktor.id)]}).status_code == 403


def test_aufgaben_zeitplan():
    from types import SimpleNamespace as N
    e = N(aufgaben_auto="woechentlich", aufgaben_auto_wochentag=0, aufgaben_auto_tag=1, aufgaben_auto_uhrzeit="07:00")
    assert versand.aufgaben_faellig(e, datetime(2026, 11, 16, 7, 0))  # Montag
    assert not versand.aufgaben_faellig(e, datetime(2026, 11, 16, 6, 59))
    assert not versand.aufgaben_faellig(e, datetime(2026, 11, 17, 8, 0))  # Dienstag
    assert versand.aufgaben_faellig(N(**{**vars(e), "aufgaben_auto": "monatlich", "aufgaben_auto_tag": 17}),
                                    datetime(2026, 11, 17, 8, 0))
    assert versand.aufgaben_faellig(N(**{**vars(e), "aufgaben_auto": "taeglich"}), datetime(2026, 11, 18, 7, 30))
    assert not versand.aufgaben_faellig(N(**{**vars(e), "aufgaben_auto": "aus"}), datetime(2026, 11, 16, 7, 30))


def test_aufgaben_automatisch_einmal_und_nur_wenn_offen(db, daten, mailbereit, schein, monkeypatch):  # noqa: F811
    from app.services import benutzer as benutzer_service
    benutzer_service.einrichten(db)  # erster Admin aus .env, falls noch niemand angelegt ist
    admin = db.scalar(select(Benutzer).where(Benutzer.benutzername == "admin"))
    admin.email, admin.aufgaben_abo = "admin@example.com", True
    db.flush()
    assert versand.aufgaben_senden(db, [admin.id], ausgeloest="automatisch", nur_neue=True).gesendet == ["admin"]
    assert versand.aufgaben_senden(db, [admin.id], ausgeloest="automatisch", nur_neue=True).gesendet == []  # heute schon
    # nichts offen → mit „nur wenn offen“ keine Mail
    from app.services import offen
    leer = {"zahlen": offen.OffenePunkte(0, 0, 0), "fristen": [], "luecken": []}
    monkeypatch.setattr(offen, "fuer_report", lambda db, **_: leer)
    assert versand.aufgaben_zahlen(leer) == []
    assert versand.aufgaben_senden(db, [admin.id], nur_wenn_offen=True).gesendet == []
    assert len(schein.gesendet) == 1


def test_aufgaben_einstellungen(angemeldet, db):
    basis = {"stichtag": "ende", "topliste_mindestmenge": "20", "strompreis_vorbelegung": "0,30"}
    angemeldet.post("/einstellungen", data=basis | {"aufgaben_auto_an": "on", "aufgaben_auto": "woechentlich", "aufgaben_auto_wochentag": "2",
                                                    "aufgaben_auto_tag": "3", "aufgaben_auto_uhrzeit": "06:30"})
    e = app_einstellungen.laden(db)
    assert (e.aufgaben_auto, e.aufgaben_auto_wochentag, e.aufgaben_auto_uhrzeit) == ("woechentlich", 2, "06:30")
    assert not e.aufgaben_auto_nur_offen  # Haken nicht gesendet = aus
    assert "wöchentlich, Mittwoch 06:30" in angemeldet.get(f"/reports?{SEP}").text
    # Schalter aus: Intervall bleibt gespeichert, wirksam „aus“
    angemeldet.post("/einstellungen", data=basis | {"aufgaben_auto": "monatlich", "aufgaben_auto_tag": "3"})
    e = app_einstellungen.laden(db)
    assert (e.aufgaben_auto, e.aufgaben_auto_intervall, e.aufgaben_auto_an) == ("aus", "monatlich", False)
    seite = angemeldet.get("/einstellungen").text
    assert 'data-zeigen-wenn="aufgaben_auto_an" hidden' in seite and '<option value="monatlich" selected>' in seite
    antwort = angemeldet.post("/einstellungen", data=basis | {"aufgaben_auto_an": "on", "aufgaben_auto": "monatlich",
                                                              "aufgaben_auto_tag": "31"})
    assert antwort.status_code == 422 and "Tag zwischen 1 und 28" in antwort.text


def test_dialog_hinweis_und_knopf(angemeldet, db):
    seite = angemeldet.get(f"/reports?{SEP}").text
    assert seite.count("Einmaliger Versand, je Adresse eine eigene Mail") == 5  # alle außer Fahrer:innen
    assert seite.count("ändert kein Abo") == 5  # alle haben ein Abo
    assert seite.count(">Per Mail senden …</button>") == 5  # Fahrer:innen-Knopf fehlt ohne Ladungen im Zeitraum


def test_freie_adressen_und_bilder(rollen, db, daten, mailbereit, schein):  # noqa: F811
    admin = rollen["admin"]
    antwort = admin.post(f"/reports/karten_cpo/senden?{SEP}", data={"adressen": "chef@example.org, kaputt@"})
    assert "ungültige Adresse: kaputt@" in antwort.text and not schein.gesendet  # nichts gesendet
    antwort = admin.post(f"/reports/karten_cpo/senden?{SEP}", data={"adressen": "chef@example.org\nCHEF@example.org; b@x.example"})
    assert "Karten und Preise je CPO 09/2026: 2 gesendet" in antwort.text  # doppelte Adresse nur einmal
    nachricht = schein.gesendet[0][1]
    assert nachricht["To"] == "chef@example.org" and nachricht["Subject"] == "Ladefalke – Karten und Preise je CPO 09/2026"
    assert nachricht.get_body(("plain",)).get_content().startswith("Hallo,")
    eintrag = db.scalars(select(ReportVersand).where(ReportVersand.an == "chef@example.org")).first()
    assert eintrag.art == "karten_cpo" and eintrag.benutzer_id is None and eintrag.empfaenger_name == "chef@example.org"
    assert set(versand.zuletzt_adressen(db)) == {"b@x.example", "chef@example.org"}
    assert 'data-adresse="chef@example.org"' in admin.get(f"/reports?{SEP}").text  # Vorschlag im Dialog
    # Flottenbilanz mit Bildern: PDF und beide Seiten als JPG
    schein.gesendet.clear()
    admin.post(f"/reports/flottenbilanz/senden?{SEP}", data={"adressen": "presse@example.com", "bilder": "on"})
    [(_, nachricht)] = schein.gesendet
    namen = [a.get_filename() for a in nachricht.iter_attachments()]
    assert namen == ["ladefalke-flottenbilanz-09-2026.pdf", "ladefalke-flottenbilanz-09-2026-seite-1.jpg",
                     "ladefalke-flottenbilanz-09-2026-seite-2.jpg"]
    assert admin.post(f"/reports/fahrer/senden?{SEP}", data={}).status_code == 200  # ohne Auswahl: Hinweis
    assert admin.post(f"/reports/unbekannt/senden?{SEP}", data={"adressen": "a@b.example"}).status_code == 404


def test_fahrer_kopie_an_mich(rollen, db, daten, mailbereit, schein):  # noqa: F811
    admin = rollen["admin"]
    daten["anna"].email = "anna@example.com"
    ich = db.scalar(select(Benutzer).where(Benutzer.benutzername == "admin"))
    ich.email = "admin@example.com"
    db.flush()
    antwort = admin.post(f"/reports/fahrer/senden?{SEP}", data={"fahrer_id": [str(daten["anna"].id)], "kopie": "on",
                                                                "adressen": "fremd@example.org"})
    assert "Ladereport 09/2026: 1 gesendet" in antwort.text
    an = [n["To"] for _, n in schein.gesendet]
    assert an == ["anna@example.com", "admin@example.com"]  # nie an freie Adressen
    assert schein.gesendet[1][1]["Subject"] == "Kopie: Dein Ladereport 09/2026 (Anna CPO)"
