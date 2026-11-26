"""Texte und Bedienung: Mehrzahl, Rückfragen, gesperrte Aktionen für Nicht-Admins, Filter zurücksetzen, Hilfe-„?“."""
from app.formatierung import anzahl, wurde
from tests.test_benutzer import rollen  # noqa: F401  (Fixture)


def test_mehrzahl():
    assert anzahl(1, "Ladevorgang") == "1 Ladevorgang"
    assert anzahl(0, "Ladevorgang") == "0 Ladevorgänge" and anzahl(3, "Gebühr") == "3 Gebühren"
    assert anzahl(1, "Ladevorgang", dativ=True) == "1 Ladevorgang"
    assert anzahl(2, "Ladevorgang", dativ=True) == "2 Ladevorgängen"
    assert (wurde(1), wurde(2)) == ("wurde", "wurden")


def test_rueckfragen_vor_folgenreichen_aktionen():
    """Rückfrage (data-bestaetigen) vor: Manuell zuweisen, Entfernen in den Einstellungen, PLZ/Ort am Ladeort."""
    from pathlib import Path
    vorlagen = Path(__file__).parent.parent / "app" / "templates"
    aufgaben = (vorlagen / "zu_klaeren.html").read_text()
    assert 'data-bestaetigen="Markierte Ladevorgänge ({n}) manuell dem gewählten Fahrzeug zuweisen?' in aufgaben
    einst = (vorlagen / "einstellungen.html").read_text()
    for form in ("cpo-stromart-loeschen-", "steuersatz-loeschen-", "spritpreis-loeschen-"):
        knopf = einst.split(f'form="{form}')[1].split(">")[0]
        assert "data-bestaetigen=" in knopf, form
    assert 'data-bestaetigen="PLZ/Ort für alle Ladevorgänge an' in (vorlagen / "ladevorgang_detail.html").read_text()


def test_einstellungen_fuer_nicht_admins_ausgegraut_statt_versteckt(rollen):  # noqa: F811
    """ADR 0017: Aktionen bleiben sichtbar und werden für Nicht-Admins (per Skript) ausgegraut – mit Grund."""
    operator = rollen["operator"]
    einst = operator.get("/einstellungen").text
    for knopf in ("Jetzt nachsehen", "Jetzt aktualisieren", "Jetzt laden", "AC/DC jetzt neu bestimmen"):
        assert knopf in einst, knopf
    assert '<div class="system-knoepfe" data-nur-admin>' in einst and '<tr data-nur-admin>' in einst
    assert "Nur für Admins – du bist als Operator angemeldet." in einst
    msp = operator.get("/stammdaten/msp/1").text
    assert "Nur lesen –" in msp and "ändern kann ein Admin" in msp
    assert "Nur lesen –" not in rollen["admin"].get("/stammdaten/msp/1").text


def test_filter_zuruecksetzen_nur_mit_filter(angemeldet):
    assert "Filter zurücksetzen" not in angemeldet.get("/ladevorgaenge?art=alles").text
    assert "Filter zurücksetzen" in angemeldet.get("/ladevorgaenge?art=alles&suche=xyz").text


def test_hilfe_als_knopf(angemeldet):
    seite = angemeldet.get("/einstellungen").text
    assert '<abbr class="hilfe" tabindex="0" role="button" aria-label="Erklärung: ' in seite
    assert 'abbr.hilfe' in angemeldet.get("/").text  # Popover-Skript in base.html
