"""Backup: Erstellen (Inhalt, Manifest, Verschlüsselung), Liste, Aufräumen, Zeitplan, Wiederherstellen, Rechte."""
import json
import os
import tarfile
from datetime import datetime
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from app.config import einstellungen
from app.services import backup
from tests.test_benutzer import rollen  # noqa: F401  (Fixture)


@pytest.fixture
def ablage(tmp_path, monkeypatch):
    monkeypatch.setattr(einstellungen(), "backup_dir", str(tmp_path / "backups"))
    monkeypatch.setattr(einstellungen(), "backup_passwort", "")
    return tmp_path / "backups"


def _inhalt(datei: Path, passwort: str | None = None) -> dict:
    roh = datei.read_bytes()
    if passwort:
        roh = backup.entschluesseln(roh, passwort)
    ziel = datei.parent / "entpackt"
    (ziel / "a.tar.gz").parent.mkdir(exist_ok=True)
    (ziel / "a.tar.gz").write_bytes(roh)
    with tarfile.open(ziel / "a.tar.gz") as tar:
        namen = tar.getnames()
        manifest = json.load(tar.extractfile("manifest.json"))
    return {"namen": namen, "manifest": manifest}


def test_erstellen_inhalt_und_liste(db, ablage):
    datei = backup.erstellen(db)
    assert datei.name.startswith("ladefalke-backup-") and datei.name.endswith(".tar.gz")
    inhalt = _inhalt(datei)
    assert {"datenbank.dump", "manifest.json", "parser/dcs.py", "parser/basis.py"} <= set(inhalt["namen"])
    m = inhalt["manifest"]
    assert m["art"] == "manuell" and m["datenbankstand"] and not m["verschluesselt"]
    assert "dcs.py" in m["parser"] and len(m["parser"]["dcs.py"]) == 64 and "msp" in m["anzahlen"]
    [s] = backup.liste()
    assert s.datei == datei and s.art == "manuell" and not s.verschluesselt and s.groesse > 0
    zweite = backup.erstellen(db)  # gleiche Sekunde: eigener Name
    assert zweite != datei and len(backup.liste()) == 2


def test_verschluesselung(db, ablage):
    datei = backup.erstellen(db, passwort="geheim-123")
    assert datei.name.endswith(".tar.gz.enc") and backup.ist_verschluesselt(datei)
    with pytest.raises(backup.BackupFehler, match="Passwort falsch"):
        backup.entschluesseln(datei.read_bytes(), "falsch")
    assert _inhalt(datei, "geheim-123")["manifest"]["verschluesselt"] is True
    assert backup.liste()[0].verschluesselt


def test_aufraeumen_nur_automatische(db, ablage):
    for _ in range(3):
        backup.erstellen(db, "automatisch")
    backup.erstellen(db, "manuell")
    weg = backup.aufraeumen(behalten=2)
    assert len(weg) == 1
    arten = sorted(s.art for s in backup.liste())
    assert arten == ["automatisch", "automatisch", "manuell"]


@pytest.mark.parametrize("zeitplan, letzter, jetzt, erwartet", [
    ("aus", None, datetime(2026, 11, 12, 3), False),
    ("taeglich", None, datetime(2026, 11, 12, 3), True),  # noch nie: sofort
    ("taeglich", datetime(2026, 11, 12, 2, 0, 5), datetime(2026, 11, 12, 23), False),  # heute schon gelaufen
    ("taeglich", datetime(2026, 11, 11, 2, 0, 5), datetime(2026, 11, 12, 1), False),  # heutiger Termin noch offen
    ("taeglich", datetime(2026, 11, 11, 2, 0, 5), datetime(2026, 11, 12, 2, 1), True),
    ("woechentlich", datetime(2026, 11, 8, 2, 0, 5), datetime(2026, 11, 14, 23), False),  # Sonntag 8.11., Sa 14.11.
    ("woechentlich", datetime(2026, 11, 8, 2, 0, 5), datetime(2026, 11, 15, 2, 30), True),  # nächster Sonntag
])
def test_zeitplan(zeitplan, letzter, jetzt, erwartet):
    assert backup.faellig(zeitplan, 6, "02:00", letzter, jetzt) is erwartet


def test_wiederherstellen_in_eigene_datenbank(db, ablage, tmp_path, monkeypatch):
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    (uploads / "rechnung.pdf").write_bytes(b"%PDF-1.4 test")
    monkeypatch.setattr(einstellungen(), "upload_dir", str(uploads))
    datei = backup.erstellen(db, passwort="pw")
    (uploads / "rechnung.pdf").unlink()
    (uploads / "fremd.pdf").write_bytes(b"x")

    basis = make_url(einstellungen().database_url)
    ziel = basis.set(database=f"{basis.database}_wh")
    admin = create_engine(basis.set(database="postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'DROP DATABASE IF EXISTS "{ziel.database}" WITH (FORCE)'))
        conn.execute(text(f'CREATE DATABASE "{ziel.database}"'))
    try:
        with pytest.raises(backup.BackupFehler, match="verschlüsselt"):
            backup.wiederherstellen(datei, None, datenbank_url=ziel.render_as_string(hide_password=False))
        bericht = backup.wiederherstellen(datei, "pw", datenbank_url=ziel.render_as_string(hide_password=False))
        assert any("Datenbank eingespielt" in z for z in bericht) and any("gleicher Stand" in z for z in bericht)
        assert sorted(p.name for p in uploads.iterdir()) == ["rechnung.pdf"]  # Originaldateien ersetzt
        pruef = create_engine(ziel)
        with pruef.connect() as conn:
            assert conn.scalar(text("SELECT count(*) FROM msp")) >= 7
            assert conn.scalar(text("SELECT version_num FROM alembic_version"))
        pruef.dispose()
    finally:
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{ziel.database}" WITH (FORCE)'))
        admin.dispose()


def test_backup_seite_und_rechte(rollen, ablage, db):  # noqa: F811
    admin, operator = rollen["admin"], rollen["operator"]
    seite = admin.get("/einstellungen").text
    assert 'id="backup"' in seite and "Kein <code>BACKUP_PASSWORT</code>" in seite and "Noch keine Sicherungen." in seite
    assert operator.post("/einstellungen/backup/erstellen").status_code == 403
    antwort = admin.post("/einstellungen/backup/erstellen")
    assert "Sicherung ladefalke-backup-" in antwort.text and "unverschlüsselt" in antwort.text
    [s] = backup.liste()
    assert operator.get(f"/einstellungen/backup/{s.name}/datei").status_code == 403
    download = admin.get(f"/einstellungen/backup/{s.name}/datei")
    assert download.status_code == 200 and download.content == s.datei.read_bytes()
    assert 'class="rechts" data-nur-admin' in operator.get("/einstellungen").text  # sichtbar, für Operator gesperrt
    assert operator.post(f"/einstellungen/backup/{s.name}/loeschen").status_code == 403
    assert "gelöscht" in admin.post(f"/einstellungen/backup/{s.name}/loeschen").text and backup.liste() == []
    assert admin.get("/einstellungen/backup/gibtsnicht/datei").status_code == 404


def test_backup_zeitplan_speichern(angemeldet, db):
    daten = {"stichtag": "ende", "topliste_mindestmenge": "20", "strompreis_vorbelegung": "0,30",
             "backup_zeitplan": "woechentlich", "backup_wochentag": "2", "backup_uhrzeit": "03:30", "backup_behalten": "4"}
    angemeldet.post("/einstellungen", data=daten)
    from app.services import app_einstellungen
    e = app_einstellungen.laden(db)
    assert (e.backup_zeitplan, e.backup_wochentag, e.backup_uhrzeit, e.backup_behalten) == ("woechentlich", 2, "03:30", 4)
    assert angemeldet.post("/einstellungen", data={**daten, "backup_uhrzeit": "25:00"}).status_code == 422


def test_vor_update(db, ablage):
    """start.sh: Sicherung nur, wenn Migrationen anstehen – einmal je Migrationsschritt; Datenbank neuer als die
    App (unbekannter Stand) bricht ab."""
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    skripte = ScriptDirectory.from_config(Config("alembic.ini"))
    kopf = skripte.get_current_head()
    datei, meldung = backup.vor_update(db)
    assert datei is None and "aktuell" in meldung and backup.liste() == []

    vorher = skripte.get_revision(kopf).down_revision
    db.execute(text("UPDATE alembic_version SET version_num = :v"), {"v": vorher})
    datei, meldung = backup.vor_update(db)
    assert datei is not None and datei.name.split(".")[0].endswith("-update") and f"{vorher} → {kopf}" in meldung
    [s] = backup.liste()
    assert s.art == backup.VOR_UPDATE and s.manifest["migration_ziel"] == kopf
    assert s.manifest["datenbankstand"] == vorher
    # Neustart nach fehlgeschlagener Migration: keine zweite Sicherung
    datei, meldung = backup.vor_update(db)
    assert datei is None and "schon da" in meldung and len(backup.liste()) == 1
    assert backup.aufraeumen(0) == []  # wird nicht wie automatische Sicherungen aufgeräumt

    db.execute(text("UPDATE alembic_version SET version_num = 'zukunft123'"))
    with pytest.raises(backup.BackupFehler, match="neuer als diese App-Version"):
        backup.vor_update(db)
