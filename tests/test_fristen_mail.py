"""Benachrichtigung bei Fristen: Abo unter „Mein Konto“, Meldung bei Hinweisfrist und bei Ablauf (je Stufe einmal),
betroffene Fahrzeuge/Personen im Text, Report „Offene Aufgaben“ im Anhang, Versandprotokoll."""
from datetime import date, datetime, timedelta

import pytest
from sqlalchemy import select

from app.config import heute
from app.models import Benutzer, Fahrzeug, FristMeldung, ReportVersand
from app.services import app_einstellungen, fristen_mail, mail


@pytest.fixture
def postfach(db, monkeypatch):
    gesendet = []
    app_einstellungen.speichern(db, {"mail_server": "smtp.example.com", "mail_absender": "app@example.com"})
    monkeypatch.setattr(mail, "senden", lambda db_, an, betreff, text, anhaenge=(): gesendet.append(
        {"an": an, "betreff": betreff, "text": text, "anhaenge": anhaenge}))
    return gesendet


def test_abo_unter_mein_konto(angemeldet, db):
    assert 'action="/konto/fristen-abo"' in angemeldet.get("/konto").text
    assert "benachrichtigt" in angemeldet.post("/konto/fristen-abo", data={"abo": "on"}).text
    assert db.scalar(select(Benutzer).where(Benutzer.benutzername == "admin")).fristen_abo
    angemeldet.post("/konto/fristen-abo", data={})
    assert not db.scalar(select(Benutzer).where(Benutzer.benutzername == "admin")).fristen_abo


def test_hinweisfrist_dann_ablauf(db, postfach):
    tag = heute()
    jetzt = datetime.combine(tag, datetime.min.time()).replace(hour=8)
    b = Benutzer(benutzername="frida", name="Frida Frist", email="frida@example.com", rolle="viewer",
                 passwort_hash="x", fristen_abo=True)
    ohne = Benutzer(benutzername="otto", name="Otto Ohne", email="otto@example.com", rolle="viewer", passwort_hash="x")
    fz = Fahrzeug(kennzeichen="FR-1", leasing_ende=tag + timedelta(days=20))  # in der Hinweisfrist (90 Tage)
    db.add_all([b, ohne, fz])
    db.flush()
    assert fristen_mail.senden(db, jetzt) == ["Frida Frist"]
    m = postfach[-1]
    assert m["an"] == "frida@example.com" and "1 in der Hinweisfrist" in m["betreff"]
    assert f"Leasing-Ende FR-1: läuft ab am {fz.leasing_ende:%d.%m.%Y} (in 20 Tagen)" in m["text"]
    assert m["anhaenge"][0][0].startswith("ladefalke-offene-aufgaben-") and m["anhaenge"][0][1].startswith(b"%PDF")
    assert fristen_mail.senden(db, jetzt) == []  # dieselbe Stufe nicht noch einmal
    # abgelaufen: zweite Meldung
    assert fristen_mail.senden(db, jetzt + timedelta(days=21)) == ["Frida Frist"]
    assert "1 abgelaufen" in postfach[-1]["betreff"] and "Leasing-Ende FR-1: abgelaufen am" in postfach[-1]["text"]
    # verlängert (neues Datum): Kette beginnt neu
    fz.leasing_ende = tag + timedelta(days=60)
    db.flush()
    assert fristen_mail.senden(db, jetzt) == ["Frida Frist"]
    assert len(db.scalars(select(FristMeldung).where(FristMeldung.benutzer_id == b.id)).all()) == 3
    eintraege = db.scalars(select(ReportVersand).where(ReportVersand.art == "fristen")).all()
    assert len(eintraege) == 3 and all(e.erfolg for e in eintraege)


def test_faellig_und_ohne_mailserver(db, monkeypatch):
    assert not fristen_mail.faellig("", datetime(2026, 11, 23, 6, 59))
    assert fristen_mail.faellig("", datetime(2026, 11, 23, 7, 0))
    assert not fristen_mail.faellig("2026-11-23", datetime(2026, 11, 23, 12))
    assert fristen_mail.faellig("2026-11-22", datetime(2026, 11, 23, 12))
    db.add(Fahrzeug(kennzeichen="FR-2", leasing_ende=heute()))
    db.add(Benutzer(benutzername="fritz", name="Fritz", email="f@example.com", rolle="viewer", passwort_hash="x",
                    fristen_abo=True))
    db.flush()
    assert fristen_mail.senden(db, datetime.now()) == []  # ohne Mailserver nichts


def test_texte():
    from app.services.fristen import Frist
    tag = date(2026, 11, 23)
    assert fristen_mail.zeile(Frist("fuehrerschein", "Max", "/x", None, tag)) == \
        "Führerscheinkontrolle Max: keine Kontrolle eingetragen"
    assert fristen_mail.zeile(Frist("fuehrerschein", "Max", "/x", date(2026, 11, 1), tag)) == \
        "Führerscheinkontrolle Max: überfällig seit 01.11.2026"
    assert fristen_mail.zeile(Frist("vignette", "W-1", "/x", date(2027, 1, 31), tag, ab=date(2027, 1, 1))) == \
        "Vignette W-1: noch nicht gültig (ab 01.01.2027)"
