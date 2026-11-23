from datetime import date
from decimal import Decimal

from sqlalchemy import select

from app.config import heute
from app.models import Benutzer, Fahrer, Fahrzeug, KennungTyp, Ladekarte, Msp, ZuordnungFahrerFahrzeug, ZuordnungKarte


def test_msp_startdaten(db):
    keys = set(db.scalars(select(Msp.parser_key)))
    assert {"hastobe_csv", "wien_energie", "dcs", "linz_ag", "salzburg_ag", "enbw"} <= keys
    standort = db.scalar(select(Msp).where(Msp.parser_key == "hastobe_csv"))
    assert standort.standard_ladeart.value == "privat"


def test_alle_seiten_erreichbar(angemeldet):
    for pfad in ["/", "/stammdaten/msp", "/stammdaten/msp/neu", "/stammdaten/ladekarten",
                 "/stammdaten/ladekarten/neu", "/stammdaten/fahrzeuge", "/stammdaten/fahrzeuge/neu",
                 "/stammdaten/fahrer", "/stammdaten/fahrer/neu", "/stammdaten/zuordnungen",
                 "/stammdaten/zuordnungen?stichtag=2026-11-04", "/import", "/auswertungen",
                 "/aufgaben", "/import/pauschalen/neu", "/ladevorgaenge"]:
        assert angemeldet.get(pfad).status_code == 200, pfad


def test_msp_anlegen_und_doppelt(angemeldet, db):
    antwort = angemeldet.post("/stammdaten/msp/neu", data={"name": "Test-MSP", "standard_ladeart": "oeffentlich"})
    assert antwort.status_code == 200
    assert "Test-MSP" in antwort.text
    antwort = angemeldet.post("/stammdaten/msp/neu", data={"name": "test-msp", "standard_ladeart": "privat"})
    assert antwort.status_code == 422
    assert "existiert bereits" in antwort.text


def test_msp_mit_karten_nicht_loeschbar(angemeldet, db):
    msp = db.scalar(select(Msp).where(Msp.parser_key == "dcs"))
    db.add(Ladekarte(msp=msp, kennung="X1", kennung_typ=KennungTyp.kartennummer))
    db.flush()
    antwort = angemeldet.post(f"/stammdaten/msp/{msp.id}/loeschen")
    assert "kann nicht gelöscht werden" in antwort.text
    assert db.get(Msp, msp.id) is not None
    seite = angemeldet.get(f"/stammdaten/msp/{msp.id}").text  # kein Knopf, nur Hinweis
    assert f"/stammdaten/msp/{msp.id}/loeschen" not in seite and "Löschen ist nicht möglich" in seite


def test_fahrzeug_crud(angemeldet, db):
    antwort = angemeldet.post("/stammdaten/fahrzeuge/neu",
                              data={"kennzeichen": " s-ab  123 ", "modell": "Testmodell", "aktiv": "on"})
    assert antwort.status_code == 200
    fz = db.scalar(select(Fahrzeug).where(Fahrzeug.kennzeichen == "S-AB 123"))
    assert fz is not None and fz.aktiv
    # Doppeltes Kennzeichen
    antwort = angemeldet.post("/stammdaten/fahrzeuge/neu", data={"kennzeichen": "S-AB 123"})
    assert antwort.status_code == 422
    # Bearbeiten: Haken „aktiv“ fehlt → inaktiv
    angemeldet.post(f"/stammdaten/fahrzeuge/{fz.id}", data={"kennzeichen": "S-AB 123", "modell": "Neu"})
    db.refresh(fz)
    assert fz.modell == "Neu" and not fz.aktiv
    assert 'name="dc_kw_schnitt"' not in angemeldet.get(f"/stammdaten/fahrzeuge/{fz.id}").text  # keine Eingabe mehr
    # Ohne Referenzen wirklich löschen
    angemeldet.post(f"/stammdaten/fahrzeuge/{fz.id}/loeschen")
    assert db.get(Fahrzeug, fz.id) is None


def test_fahrzeug_mit_zuordnung_wird_deaktiviert(angemeldet, db):
    fz, fahrer = Fahrzeug(kennzeichen="S-DE 1"), Fahrer(name="Test")
    db.add_all([fz, fahrer])
    db.flush()
    db.add(ZuordnungFahrerFahrzeug(fahrer_id=fahrer.id, fahrzeug_id=fz.id, gueltig_ab=date(2026, 1, 1)))
    db.flush()
    antwort = angemeldet.post(f"/stammdaten/fahrzeuge/{fz.id}/loeschen")
    assert "deaktiviert statt gelöscht" in antwort.text
    db.refresh(fz)
    assert not fz.aktiv
    # Knopf zeigt, was er tut: aktiv mit Daten → Deaktivieren, inaktiv → Wieder aktivieren
    assert ">Wieder aktivieren</button>" in angemeldet.get(f"/stammdaten/fahrzeuge/{fz.id}").text
    angemeldet.post(f"/stammdaten/fahrzeuge/{fz.id}/aktivieren")
    db.refresh(fz)
    assert fz.aktiv
    seite = angemeldet.get(f"/stammdaten/fahrzeuge/{fz.id}").text
    assert 'gefahr">Deaktivieren</button>' in seite and 'gefahr">Löschen</button>' not in seite
    seite = angemeldet.get(f"/stammdaten/fahrer/{fahrer.id}").text
    assert 'gefahr">Deaktivieren</button>' in seite


def test_ohne_daten_loeschen_knopf(angemeldet, db):
    fz = Fahrzeug(kennzeichen="S-LO 1")
    db.add(fz)
    db.flush()
    seite = angemeldet.get(f"/stammdaten/fahrzeuge/{fz.id}").text
    assert 'gefahr">Löschen</button>' in seite and "endgültig löschen?" in seite


def test_ladekarte_anlegen_und_zuordnen(angemeldet, db):
    msp = db.scalar(select(Msp).where(Msp.parser_key == "linz_ag"))
    fz = Fahrzeug(kennzeichen="L-ZU 1")
    db.add(fz)
    db.flush()
    antwort = angemeldet.post("/stammdaten/ladekarten/neu", data={
        "msp_id": str(msp.id), "kennung": "123456", "kennung_typ": "kartennummer", "aktiv": "on"})
    assert antwort.status_code == 200
    karte = db.scalar(select(Ladekarte).where(Ladekarte.kennung == "123456"))
    assert karte.kennung_typ == KennungTyp.kartennummer

    ab = heute().isoformat()
    antwort = angemeldet.post(f"/stammdaten/ladekarten/{karte.id}/zuordnung",
                              data={"ziel": f"fahrzeug:{fz.id}", "ab": ab, "bis": ""})
    assert antwort.status_code == 200
    assert "zugeordnet" in antwort.text
    assert db.scalar(select(ZuordnungKarte).where(ZuordnungKarte.ladekarte_id == karte.id)).fahrzeug_id == fz.id

    # Ohne Ziel → Fehlermeldung im Formular
    antwort = angemeldet.post(f"/stammdaten/ladekarten/{karte.id}/zuordnung", data={"ziel": "", "ab": ab})
    assert antwort.status_code == 422
    assert "genau eine" in antwort.text

    # Überlappende befristete Periode → Fehlermeldung
    antwort = angemeldet.post(f"/stammdaten/ladekarten/{karte.id}/zuordnung",
                              data={"ziel": f"fahrzeug:{fz.id}", "ab": ab, "bis": ab})
    assert antwort.status_code == 422
    assert "Überschneidung" in antwort.text


def test_ladekarte_doppelte_kennung(angemeldet, db):
    msp = db.scalar(select(Msp).where(Msp.parser_key == "enbw"))
    daten = {"msp_id": str(msp.id), "kennung": "DE-ABC-1", "kennung_typ": "kartennummer"}
    angemeldet.post("/stammdaten/ladekarten/neu", data=daten)
    antwort = angemeldet.post("/stammdaten/ladekarten/neu", data=daten)
    assert antwort.status_code == 422
    assert "existiert bei diesem MSP bereits" in antwort.text


def test_gastkarte_nur_ohne_zuordnungen(angemeldet, db):
    msp = db.scalar(select(Msp).where(Msp.parser_key == "hastobe_csv"))
    karte, fz = Ladekarte(msp=msp, kennung="Label A", kennung_typ=KennungTyp.kartenlabel), Fahrzeug(kennzeichen="X 1")
    db.add_all([karte, fz])
    db.flush()
    db.add(ZuordnungKarte(ladekarte_id=karte.id, fahrzeug_id=fz.id, gueltig_ab=date(2026, 1, 1)))
    db.flush()
    db.expire(karte)
    antwort = angemeldet.post(f"/stammdaten/ladekarten/{karte.id}", data={
        "msp_id": str(msp.id), "kennung": "Label A", "kennung_typ": "kartenlabel", "ist_gastkarte": "on"})
    assert antwort.status_code == 422
    assert "nur ohne Zuordnungen" in antwort.text


def test_fahrer_fahrzeug_zuordnen_ueber_formular(angemeldet, db):
    fahrer, fz = Fahrer(name="Formular Test"), Fahrzeug(kennzeichen="W-FT 1")
    db.add_all([fahrer, fz])
    db.flush()
    antwort = angemeldet.post(f"/stammdaten/fahrer/{fahrer.id}/fahrzeug",
                              data={"fahrzeug_id": str(fz.id), "ab": "2026-01-01"})
    assert antwort.status_code == 200
    assert "rückwirkend" in antwort.text  # Warnhinweis
    antwort = angemeldet.get(f"/stammdaten/fahrzeuge/{fz.id}")
    assert "Formular Test" in antwort.text
    assert "01.01.2026 – offen" in antwort.text


def test_zuordnung_bearbeiten_und_loeschen(angemeldet, db):
    fahrer, fz = Fahrer(name="Bearbeiten Test"), Fahrzeug(kennzeichen="W-BT 1")
    db.add_all([fahrer, fz])
    db.flush()
    z = ZuordnungFahrerFahrzeug(fahrer_id=fahrer.id, fahrzeug_id=fz.id, gueltig_ab=date(2026, 1, 1))
    db.add(z)
    db.flush()
    zurueck = f"/stammdaten/fahrer/{fahrer.id}"
    antwort = angemeldet.post(f"/stammdaten/zuordnungen/fahrer/{z.id}?zurueck={zurueck}",
                              data={"fahrzeug_id": str(fz.id), "ab": "2026-01-01", "bis": "2026-06-30"},
                              follow_redirects=False)
    assert antwort.headers["location"] == zurueck
    db.refresh(z)
    assert z.gueltig_bis == date(2026, 6, 30)
    angemeldet.post(f"/stammdaten/zuordnungen/fahrer/{z.id}/loeschen")
    assert db.get(ZuordnungFahrerFahrzeug, z.id) is None


def test_schaetzwerte_nur_auf_der_fahrzeugseite(angemeldet, db):
    fz = Fahrzeug(kennzeichen="W-LISTE 1")
    db.add(fz)
    db.flush()
    text = angemeldet.get("/stammdaten/fahrzeuge").text
    assert "Ø Ladeleistung" not in text and "Schätzung Obergrenze" not in text  # keine Technik-Spalten mehr
    seite = angemeldet.get(f"/stammdaten/fahrzeuge/{fz.id}").text
    # ohne Daten: Vorgaben in der gemeinsamen Tabelle „Ladeprofil und kWh-Schätzung“
    assert "Ladeprofil und kWh-Schätzung" in seite
    assert "11,0 kW, höchstens 50,0 kWh" in seite and "90,0 kW, höchstens 50,0 kWh" in seite


def test_fahrzeugliste_filter_modell(angemeldet, db):
    db.add_all([Fahrzeug(kennzeichen="W-MOD 1", modell="Testmodell A"), Fahrzeug(kennzeichen="W-MOD 2", modell="Testmodell B"),
                Fahrzeug(kennzeichen="W-MOD 3")])
    db.flush()
    text = angemeldet.get("/stammdaten/fahrzeuge?modell=Testmodell+A").text
    assert "W-MOD 1" in text and "W-MOD 2" not in text and "W-MOD 3" not in text
    assert 'name="modell" value="Testmodell A" checked> Testmodell A' in text
    assert 'href="/stammdaten/fahrzeuge?modell=Testmodell+A&amp;status=alle"' in text  # Status behält das Modell
    text = angemeldet.get("/stammdaten/fahrzeuge?modell=-").text
    assert "W-MOD 3" in text and "W-MOD 1" not in text
    # Mehrfachauswahl: Modell A oder ohne Modell; der Status-Link behält beide
    text = angemeldet.get("/stammdaten/fahrzeuge?modell=Testmodell+A&modell=-").text
    assert "W-MOD 1" in text and "W-MOD 3" in text and "W-MOD 2" not in text and "2 ausgewählt" not in text
    assert "Testmodell A, ohne Modell</span></summary>" in text
    assert 'href="/stammdaten/fahrzeuge?modell=Testmodell+A&amp;modell=-&amp;status=alle"' in text


def test_status_umschalter(angemeldet):
    """Aktiv/Inaktiv/Alle als Umschalter; die MSP-Auswahl bleibt beim Umschalten erhalten."""
    text = angemeldet.get("/stammdaten/ladekarten?status=inaktiv&msp_id=1&msp_id=2").text
    assert '<nav class="umschalter" aria-label="Status">' in text
    assert '<a href="/stammdaten/ladekarten?msp_id=1&amp;msp_id=2&amp;status=alle" >Alle</a>' in text
    assert 'status=inaktiv" aria-current="true">Inaktive</a>' in text
    for basis in ("/stammdaten/fahrer", "/stammdaten/fahrzeuge"):
        assert 'aria-current="true">Aktive</a>' in angemeldet.get(basis).text


def test_theme_umschalten(angemeldet):
    """Theme hell/dunkel/auto je Benutzer unter „Mein Konto“; gilt sofort für alle Seiten (data-theme am <html>)."""
    seite = angemeldet.get("/konto").text
    assert 'value="auto" aria-pressed="true"' in seite and "title=\"Theme: Auto\"" in seite and '<html lang="de">' in seite
    assert 'class="theme-wahl" hx-boost="false"' in seite  # sonst tauscht htmx nur den body und das Theme bleibt
    assert "Theme" not in angemeldet.get("/einstellungen").text.split("<h1>")[1]  # nicht mehr global
    angemeldet.post("/konto/theme", data={"theme": "dunkel"})
    assert '<html lang="de" data-theme="dark">' in angemeldet.get("/stammdaten/fahrer").text
    angemeldet.post("/konto/theme", data={"theme": "hell"})
    seite = angemeldet.get("/konto").text
    assert '<html lang="de" data-theme="light">' in seite and 'value="hell" aria-pressed="true"' in seite
    angemeldet.post("/konto/theme", data={"theme": "unsinn"})
    assert '<html lang="de">' in angemeldet.get("/").text


def test_blaettern_folgt_liste_und_filter(angemeldet, db):
    """Detailseite: ‹ › in der Reihenfolge der Liste, mit deren zuletzt gemerktem Filter."""
    a, b, c = (Fahrzeug(kennzeichen=k, aktiv=aktiv) for k, aktiv in (("BL-A 1", True), ("BL-B 2", False),
                                                                     ("BL-C 3", True)))
    db.add_all([a, b, c])
    db.flush()
    angemeldet.get("/stammdaten/fahrzeuge?status=aktiv")  # Filter merken: nur aktive
    seite = angemeldet.get(f"/stammdaten/fahrzeuge/{a.id}").text
    assert f'href="/stammdaten/fahrzeuge/{c.id}"' in seite and "Nächste: BL-C 3" in seite  # B (inaktiv) übersprungen
    assert 'href="/stammdaten/fahrzeuge?status=aktiv"' in seite  # „← Alle Fahrzeuge“ zur gefilterten Liste
    # Inaktives Fahrzeug liegt nicht in der gefilterten Liste → Blättern in allen
    seite = angemeldet.get(f"/stammdaten/fahrzeuge/{b.id}").text
    assert "Vorherige: BL-A 1" in seite and "Nächste: BL-C 3" in seite
    angemeldet.get("/stammdaten/fahrzeuge?status=alle")
    seite = angemeldet.get(f"/stammdaten/fahrzeuge/{a.id}").text
    assert "Nächste: BL-B 2" in seite
    # Neues Fahrzeug: keine Blätter-Knöpfe
    assert 'class="detail-blaettern"' not in angemeldet.get("/stammdaten/fahrzeuge/neu").text


def test_blaettern_auf_allen_detailseiten(angemeldet, db):
    msp = db.scalar(select(Msp).where(Msp.parser_key == "dcs"))
    karten = [Ladekarte(msp=msp, kennung=f"BL{i}", kennung_typ=KennungTyp.kartennummer) for i in range(2)]
    personen = [Fahrer(name=f"Blätter {i}") for i in range(2)]
    db.add_all(karten + personen)
    db.flush()
    angemeldet.post("/benutzer/neu", data={"benutzername": "blaettern", "name": "Blättern", "rolle": "viewer",
                                           "passwort": "sehr-geheim-123", "wiederholung": "sehr-geheim-123"})
    benutzer_id = db.scalar(select(Benutzer.id).where(Benutzer.benutzername == "blaettern"))
    for pfad in [f"/stammdaten/ladekarten/{karten[0].id}", f"/stammdaten/fahrer/{personen[0].id}",
                 f"/stammdaten/msp/{msp.id}", f"/benutzer/{benutzer_id}"]:
        seite = angemeldet.get(pfad)
        assert seite.status_code == 200 and 'class="detail-blaettern"' in seite.text, pfad


def test_offene_punkte_auf_detailseiten(angemeldet, db):
    """Detailseiten zeigen dieselben offenen Punkte wie die Spalte „Hinweise“ der Listen."""
    fz = Fahrzeug(kennzeichen="OP-1", aktiv=True)
    person = Fahrer(name="Offen Punkt")
    db.add_all([fz, person])
    db.flush()
    seite = angemeldet.get(f"/stammdaten/fahrzeuge/{fz.id}").text
    assert 'class="meldung meldung-warnung offene-punkte"' in seite  # rot: ohne Fahrer:in
    assert "keine Leasingdaten" in seite and "ohne Fahrer:in" in seite
    assert "E-Mail fehlt" in angemeldet.get(f"/stammdaten/fahrer/{person.id}").text
    # Ohne offene Punkte kein Kasten
    person.email = "offen@example.org"
    db.flush()
    assert "offene-punkte" not in angemeldet.get(f"/stammdaten/fahrer/{person.id}").text


def test_ladekarte_erster_und_letzter_ladevorgang(angemeldet, db):
    """Liste: letzter Ladevorgang je Karte; Detailseite: Kennzahlen, erster und letzter Ladevorgang mit Link."""
    from datetime import datetime
    from decimal import Decimal as Dec
    from app.models import Import, ImportStatus, Ladeart, Ladevorgang, ZuordnungQuelle
    msp = db.scalar(select(Msp).where(Msp.parser_key == "dcs"))
    karte, leer = (Ladekarte(msp=msp, kennung=k, kennung_typ=KennungTyp.kartennummer) for k in ("EL-1", "EL-2"))
    imp = Import(dateiname="e.pdf", datei_pfad="e.pdf", datei_hash="e" * 64, msp_id=msp.id,
                 ladeart=Ladeart.oeffentlich, status=ImportStatus.abgeschlossen)
    db.add_all([karte, leer, imp])
    db.flush()
    vorgaenge = [Ladevorgang(import_id=imp.id, msp_id=msp.id, ladekarte_id=karte.id, roh_kartenkennung="EL-1",
                             start=datetime(2026, m, 3, 8), ende=datetime(2026, m, 3, 9, 30), kwh=Dec(20),
                             kosten_netto=Dec(8), ladeart=Ladeart.oeffentlich,
                             zuordnung_quelle=ZuordnungQuelle.keine) for m in (5, 2, 9)]
    db.add_all(vorgaenge)
    db.flush()
    liste = angemeldet.get("/stammdaten/ladekarten?status=alle").text
    assert "Letzter Ladevorgang" in liste and "03.09.2026" in liste
    from app.formatierung import vor_tagen
    assert f"03.09.2026 <small class=\"vor-tagen\">({vor_tagen(datetime(2026, 9, 3))})</small>" in liste
    seite = angemeldet.get(f"/stammdaten/ladekarten/{karte.id}").text
    erster, letzter = vorgaenge[1], vorgaenge[2]
    assert f'href="/ladevorgaenge/{erster.id}">03.02.2026 09:30' in seite
    assert f'href="/ladevorgaenge/{letzter.id}">03.09.2026 09:30' in seite
    assert '</svg>3</span><span>Ladevorgänge' in seite  # Anzahl in den Kennzahl-Kacheln
    assert "Erster Ladevorgang" not in angemeldet.get(f"/stammdaten/ladekarten/{leer.id}").text


def test_ladekarten_liste_fahrzeugkarte_mit_person(angemeldet, db):
    """„Aktuell zugeordnet“: bei Fahrzeugkarten Kennzeichen und wer das Fahrzeug heute fährt."""
    from datetime import date
    msp = db.scalar(select(Msp).where(Msp.parser_key == "dcs"))
    fz, leer_fz = Fahrzeug(kennzeichen="KP-1", aktiv=True), Fahrzeug(kennzeichen="KP-2", aktiv=True)
    person = Fahrer(name="Karla Person")
    k1, k2 = (Ladekarte(msp=msp, kennung=k, kennung_typ=KennungTyp.kartennummer) for k in ("KP-K1", "KP-K2"))
    db.add_all([fz, leer_fz, person, k1, k2])
    db.flush()
    db.add_all([ZuordnungFahrerFahrzeug(fahrer_id=person.id, fahrzeug_id=fz.id, gueltig_ab=date(2020, 1, 1)),
                ZuordnungKarte(ladekarte_id=k1.id, fahrzeug_id=fz.id, gueltig_ab=date(2020, 1, 1)),
                ZuordnungKarte(ladekarte_id=k2.id, fahrzeug_id=leer_fz.id, gueltig_ab=date(2020, 1, 1))])
    db.flush()
    liste = angemeldet.get("/stammdaten/ladekarten").text
    assert f'>KP-1</a> (<a href="/stammdaten/fahrer/{person.id}">Karla Person</a>)' in liste
    assert '>KP-2</a> <small class="ohne-fahrer">(ohne Fahrer:in)</small>' in liste


def test_zeitraum_vorauswahl(angemeldet):
    """Vorausgewählter Zeitraum je Benutzer unter „Mein Konto“: gilt für Dashboard, Auswertungen und Reports, wenn die
    Adresse keinen Zeitraum nennt; eine Angabe in der Adresse geht vor."""
    from app.services.auswertung import vormonat, zeitraum_mit
    seite = angemeldet.get("/konto").text
    assert 'name="zeitraum" value="monat" aria-pressed="true"' in seite
    monat, quartal = vormonat(), zeitraum_mit(vormonat().von, "quartal")
    assert monat.label in angemeldet.get("/").text
    angemeldet.post("/konto/zeitraum", data={"zeitraum": "quartal"})
    assert 'name="zeitraum" value="quartal" aria-pressed="true"' in angemeldet.get("/konto").text
    for pfad in ("/", "/auswertungen", "/reports"):
        assert quartal.label in angemeldet.get(pfad).text, pfad
    assert monat.label in angemeldet.get("/?art=monat").text  # Adresse geht vor
    # Ladevorgänge: ohne Parameter (Menü) die Vorauswahl, mit Filter ohne Zeitraum „Alles“
    assert 'aria-current="true">Quartal</a>' in angemeldet.get("/ladevorgaenge").text
    assert 'aria-current="true">Alles</a>' in angemeldet.get("/ladevorgaenge?suche=x").text
    angemeldet.post("/konto/zeitraum", data={"zeitraum": "alles"})
    assert "Alles (" in angemeldet.get("/auswertungen").text
    angemeldet.post("/konto/zeitraum", data={"zeitraum": "unsinn"})
    assert 'name="zeitraum" value="monat" aria-pressed="true"' in angemeldet.get("/konto").text
