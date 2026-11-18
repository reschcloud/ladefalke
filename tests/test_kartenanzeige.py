"""Anzeigename der Ladekarten: Person bzw. Kennzeichen (Person) statt Kennung, Hauptzuordnung nach Vorgängen,
weitere als „+n“, Eindeutigkeit, Sicht einer Person, Gäste, ohne Zuordnung, je Ladevorgang am Ladetag."""
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import select

from app.models import (Fahrer, Fahrzeug, Import, ImportStatus, KennungTyp, Ladeart, Ladekarte, Ladevorgang, Msp,
                        ZuordnungFahrerFahrzeug, ZuordnungKarte, ZuordnungQuelle)
from app.services import kartenanzeige

SEP = (date(2026, 9, 1), date(2026, 9, 30))


def _grund(db):
    msp = db.scalar(select(Msp).where(Msp.parser_key == "dcs"))
    paula, quirin = Fahrer(name="Paula"), Fahrer(name="Quirin")
    fz = Fahrzeug(kennzeichen="W-1")
    karten = {n: Ladekarte(msp=msp, kennung=f"AT-B-1000{i}-{i}", kennung_typ=KennungTyp.vertragsnummer,
                           bezeichnung=f"DCS AT-B-1000{i}-{i} BMW Active Vertrag sehr lang {i}")
              for i, n in enumerate(("p1", "p2", "fz", "wechsel", "ohne"))}
    gast = Ladekarte(msp=msp, kennung="GAST", kennung_typ=KennungTyp.kartenlabel, ist_gastkarte=True)
    imp = Import(dateiname="x.pdf", datei_pfad="x.pdf", datei_hash="k" * 64, msp_id=msp.id,
                 ladeart=Ladeart.oeffentlich, status=ImportStatus.abgeschlossen)
    db.add_all([paula, quirin, fz, gast, imp, *karten.values()])
    db.flush()
    db.add_all([ZuordnungKarte(ladekarte_id=karten["p1"].id, fahrer_id=paula.id, gueltig_ab=date(2026, 1, 1)),
                ZuordnungKarte(ladekarte_id=karten["p2"].id, fahrer_id=paula.id, gueltig_ab=date(2026, 1, 1)),
                ZuordnungKarte(ladekarte_id=karten["fz"].id, fahrzeug_id=fz.id, gueltig_ab=date(2026, 1, 1)),
                ZuordnungFahrerFahrzeug(fahrer_id=quirin.id, fahrzeug_id=fz.id, gueltig_ab=date(2026, 1, 1)),
                # Wechsel Mitte September: erst Paula (1 Ladung), dann Quirin (2 Ladungen)
                ZuordnungKarte(ladekarte_id=karten["wechsel"].id, fahrer_id=paula.id, gueltig_ab=date(2026, 1, 1),
                               gueltig_bis=date(2026, 9, 14)),
                ZuordnungKarte(ladekarte_id=karten["wechsel"].id, fahrer_id=quirin.id, gueltig_ab=date(2026, 9, 15))])
    vorgaenge = []
    for tag in (3, 20, 25):
        v = Ladevorgang(import_id=imp.id, msp_id=msp.id, ladekarte_id=karten["wechsel"].id, start=datetime(2026, 9, tag, 8),
                        ende=datetime(2026, 9, tag, 9), kwh=Decimal(10), kosten_netto=Decimal(5),
                        ladeart=Ladeart.oeffentlich, zuordnung_quelle=ZuordnungQuelle.keine)
        db.add(v)
        vorgaenge.append(v)
    db.flush()
    return msp, paula, quirin, karten, gast, vorgaenge


def test_zeitraum(db):
    msp, paula, quirin, karten, gast, _ = _grund(db)
    a = kartenanzeige.fuer_zeitraum(db, [*karten.values(), gast], *SEP)
    assert a[karten["p1"].id].name == "Paula" and a[karten["p1"].id].art == "person"
    # zwei Karten derselben Person beim selben MSP: Ende der Kennung dazu
    assert a[karten["p1"].id].zusatz == "000-0" and a[karten["p2"].id].zusatz == "001-1"
    assert a[karten["fz"].id].name == "W-1 (Quirin)" and a[karten["fz"].id].kennzeichen == "W-1"
    w = a[karten["wechsel"].id]
    assert w.name == "Quirin" and w.weitere == ["Paula"] and "im Zeitraum auch: Paula" in w.tooltip  # meiste Ladungen
    assert a[gast.id].name == "Gäste"
    assert a[karten["ohne"].id].name.endswith("…") and len(a[karten["ohne"].id].name) <= 32  # gekürzte Bezeichnung
    assert a[karten["p1"].id].msp == msp.name and "AT-B-10000-0" in a[karten["p1"].id].tooltip


def test_sicht_einer_person(db):
    _, paula, quirin, karten, _, _ = _grund(db)
    a = kartenanzeige.fuer_zeitraum(db, karten.values(), *SEP)
    assert a[karten["p1"].id].relativ(paula.id) == "persönlich"
    assert a[karten["fz"].id].relativ(quirin.id) == "W-1"
    assert a[karten["p1"].id].relativ(quirin.id) == "Paula"


def test_je_vorgang_am_ladetag(db):
    _, _, _, _, _, vorgaenge = _grund(db)
    a = kartenanzeige.fuer_vorgaenge(db, vorgaenge)
    assert [a[v.id].name for v in vorgaenge] == ["Paula", "Quirin", "Quirin"]
    assert vorgaenge[0].karte_anzeige.name == "Paula" and not a[vorgaenge[0].id].weitere


def test_liste_ohne_kennung(angemeldet, db):
    _, _, _, karten, _, _ = _grund(db)
    seite = angemeldet.get("/ladevorgaenge?art=monat&jahr=2026&nr=9").text
    zelle = seite.split(f'href="/stammdaten/ladekarten/{karten["wechsel"].id}"')[1].split("</td>")[0]
    assert zelle.startswith(">Paula</a>") or zelle.startswith(">Quirin</a>")
    assert 'class="karte-msp"' in zelle and "BMW Active" not in zelle


def test_fahrerseite_mit_fahrzeugkarten(angemeldet, db):
    _, _, quirin, karten, _, _ = _grund(db)
    seite = angemeldet.get(f"/stammdaten/fahrer/{quirin.id}").text
    assert "über Fahrzeug W-1" in seite and f'/stammdaten/ladekarten/{karten["fz"].id}' in seite  # Karte am Fahrzeug
    assert "· persönlich" in seite  # eigene Karte (Wechselkarte ab 15.09.)
