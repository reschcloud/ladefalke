"""Zeitlich versionierte Zuordnungen (SPEC Abschnitt 5).

Perioden sind Datumsbereiche mit inklusivem Ende; `gueltig_bis = None` bedeutet offen.
Die Datenbank verhindert Überlappungen per EXCLUDE-Constraint – hier wird vorher
geprüft, um verständliche Fehlermeldungen zu liefern.

Aneinandergrenzende Perioden mit gleichem Inhalt (dieselbe Person am selben Fahrzeug, dieselbe Karte am selben Ziel,
„ohne Fahrer:in“ am selben Fahrzeug) werden beim Speichern zu einer zusammengefasst (`zusammenfassen`).
Ein Fahrzeug kann Zeiträume „ohne Fahrer:in“ haben (FahrzeugOhneFahrer): bewusst niemandem zugeordnet, daher keine
Lücke. Sie dürfen sich nicht mit Fahrer:in-Zuordnungen des Fahrzeugs überschneiden; ein offener Zeitraum „ohne
Fahrer:in“ endet automatisch, wenn das Fahrzeug ab einem späteren Tag offen jemandem zugeordnet wird.
Gegenstück bei der Person: Zeiträume „ohne Dienstwagen“ (FahrerOhneFahrzeug, mit Grund) – Ladungen mit ihrer
persönlichen Karte sind dann begründet ohne Fahrzeug. Sie dürfen sich nicht mit Fahrzeug-Zuordnungen der Person
überschneiden; ein offener Zeitraum endet automatisch, wenn die Person ab einem späteren Tag offen ein Fahrzeug bekommt.
"""
from collections import defaultdict
from contextlib import contextmanager
from datetime import date, timedelta
from functools import wraps

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session, selectinload

from app.formatierung import datum, zeitraum
from app.models import (Fahrer, FahrerOhneFahrzeug, Fahrzeug, FahrzeugOhneFahrer, Ladekarte, ZuordnungFahrerFahrzeug,
                        ZuordnungKarte)


class ZuordnungsFehler(ValueError):
    pass


def ueberlappt(ab1: date, bis1: date | None, ab2: date, bis2: date | None) -> bool:
    return ab1 <= (bis2 or date.max) and ab2 <= (bis1 or date.max)


# ------------------------------------------------------------------ Vorladen für Auswertungen
# Auswertungen fragen je Ladevorgang „wem gehörte die Karte an diesem Tag“ und „wer fuhr das Fahrzeug“ – bei einem
# Jahr tausende Einzelabfragen (auf langsamen Servern viele Sekunden). Die Zuordnungstabellen sind klein: innerhalb
# von `vorgeladen` werden sie einmal geladen und die Fragen im Speicher beantwortet. Nur für rein lesende
# Berechnungen – Änderungen an Zuordnungen innerhalb des Blocks sieht der Speicher nicht.

SPEICHER = "zuordnungen_vorgeladen"


@contextmanager
def vorgeladen(db: Session):
    if SPEICHER in db.info:  # verschachtelt: der äußere Block hat schon geladen
        yield
        return
    karten, fahrer_fz, fz_fahrer = defaultdict(list), defaultdict(list), defaultdict(list)
    for z in db.scalars(select(ZuordnungKarte).options(selectinload(ZuordnungKarte.fahrer),
                                                       selectinload(ZuordnungKarte.fahrzeug))
                        .order_by(ZuordnungKarte.id)):
        karten[z.ladekarte_id].append(z)
    for z in db.scalars(select(ZuordnungFahrerFahrzeug).options(selectinload(ZuordnungFahrerFahrzeug.fahrer),
                                                                selectinload(ZuordnungFahrerFahrzeug.fahrzeug))
                        .order_by(ZuordnungFahrerFahrzeug.id)):
        fahrer_fz[z.fahrer_id].append(z)
        fz_fahrer[z.fahrzeug_id].append(z)
    ohne_dw = defaultdict(list)
    for o in db.scalars(select(FahrerOhneFahrzeug).order_by(FahrerOhneFahrzeug.id)):
        ohne_dw[o.fahrer_id].append(o)
    db.info[SPEICHER] = (karten, fahrer_fz, fz_fahrer, ohne_dw)
    try:
        yield
    finally:
        db.info.pop(SPEICHER, None)


def mit_vorgeladenen_zuordnungen(funktion):
    """Dekorator für lesende Berechnungen mit der Session als erstem Argument."""
    @wraps(funktion)
    def mit_speicher(db: Session, *args, **kwargs):
        with vorgeladen(db):
            return funktion(db, *args, **kwargs)
    return mit_speicher


def vorgeladen_fuer_route(route):
    """Dekorator für lesende Seiten mit `db` als Argument (z. B. Ladevorgänge-Liste: Person je Vorgang)."""
    @wraps(route)
    def mit_speicher(*args, **kwargs):
        with vorgeladen(kwargs["db"]):
            return route(*args, **kwargs)
    return mit_speicher


def _gilt(z, stichtag: date) -> bool:
    return z.gueltig_ab <= stichtag and (z.gueltig_bis is None or z.gueltig_bis >= stichtag)


def gueltig_am(model, stichtag: date):
    """SQL-Bedingung: Periode von `model` ist am Stichtag gültig."""
    return and_(model.gueltig_ab <= stichtag,
                or_(model.gueltig_bis.is_(None), model.gueltig_bis >= stichtag))


def _pruefen(bestehende, ab: date, bis: date | None, *, vorperiode_beenden: bool):
    """Prüft eine neue/geänderte Periode gegen die übrigen Perioden desselben Schlüssels.

    Liefert die offenen Vorperioden, die auf `ab − 1 Tag` beendet werden müssen.
    Automatisches Beenden nur, wenn die neue Periode selbst offen ist – eine befristete
    Periode mitten in einer offenen wäre wahrscheinlich ein Versehen.
    """
    if bis is not None and bis < ab:
        raise ZuordnungsFehler("Das Ende darf nicht vor dem Beginn liegen.")
    zu_beenden = []
    for z in bestehende:
        z_bis = z.gueltig_bis
        if vorperiode_beenden and bis is None and z_bis is None and z.gueltig_ab < ab:
            zu_beenden.append(z)
            z_bis = ab - timedelta(days=1)
        if ueberlappt(ab, bis, z.gueltig_ab, z_bis):
            raise ZuordnungsFehler(
                f"Überschneidung mit bestehender Zuordnung {zeitraum(z.gueltig_ab, z.gueltig_bis)}.")
    return zu_beenden


def _beenden(db: Session, zu_beenden, ab: date, beschreibung) -> list[str]:
    hinweise = []
    for z in zu_beenden:
        z.gueltig_bis = ab - timedelta(days=1)
        hinweise.append(f"Bisherige Zuordnung ({beschreibung(z)}) wurde zum {datum(z.gueltig_bis)} beendet.")
    db.flush()  # vor dem Einfügen, sonst greift der EXCLUDE-Constraint
    return hinweise


def zusammenfassen(db: Session, model, **schluessel) -> list[str]:
    """Perioden von `model` mit diesem Schlüssel, die direkt aneinandergrenzen (Ende + 1 Tag = Beginn), zu einer
    zusammenfassen: die frühere wird verlängert, die spätere gelöscht. Liefert Hinweise."""
    perioden = db.scalars(select(model).filter_by(**schluessel).order_by(model.gueltig_ab)).all()
    hinweise, vorige = [], None
    for z in perioden:
        if vorige is not None and vorige.gueltig_bis is not None and z.gueltig_ab == vorige.gueltig_bis + timedelta(days=1):
            bis = z.gueltig_bis
            db.delete(z)
            db.flush()  # vor dem Verlängern, sonst greift der EXCLUDE-Constraint
            vorige.gueltig_bis = bis
            db.flush()
            hinweise.append(f"Mit der angrenzenden Zuordnung zu einer zusammengefasst: "
                            f"{zeitraum(vorige.gueltig_ab, vorige.gueltig_bis)}.")
        else:
            vorige = z
    return hinweise


def _rueckwirkend(ab: date, heute: date) -> list[str]:
    if ab < heute:
        return [f"Achtung: Die Zuordnung gilt rückwirkend ab {datum(ab)}. Bereits importierte "
                "Ladevorgänge werden nur mit der Option „neu zuordnen“ angepasst."]
    return []


# ------------------------------------------------------------------ Ladekarte → Fahrer:in / Fahrzeug

def _karte_pruefen(karte: Ladekarte, fahrer_id: int | None, fahrzeug_id: int | None) -> None:
    if karte.ist_gastkarte:
        raise ZuordnungsFehler("Gastkarten werden keiner Person und keinem Fahrzeug zugeordnet.")
    if (fahrer_id is None) == (fahrzeug_id is None):
        raise ZuordnungsFehler("Bitte genau eine Fahrer:in oder ein Fahrzeug wählen.")


def _karten_perioden(db: Session, karte_id: int, ohne_id: int | None = None):
    q = select(ZuordnungKarte).where(ZuordnungKarte.ladekarte_id == karte_id)
    if ohne_id is not None:
        q = q.where(ZuordnungKarte.id != ohne_id)
    return db.scalars(q).all()


def leasing_pruefen(fahrzeug: Fahrzeug, ab: date, bis: date | None) -> tuple[date | None, list[str]]:
    """Ladekarte → Fahrzeug nur innerhalb des Leasingzeitraums (ohne Leasingdaten keine Grenze). Ein offenes Ende wird
    auf das Leasing-Ende begrenzt. Liefert (wirksames Ende, Hinweise); außerhalb → ZuordnungsFehler."""
    lb, le = fahrzeug.leasing_beginn, fahrzeug.leasing_ende
    if lb and ab < lb:
        raise ZuordnungsFehler(f"Fahrzeug {fahrzeug.kennzeichen}: Die Zuordnung beginnt vor dem Leasingbeginn "
                               f"{datum(lb)} – Ladekarten nur innerhalb des Leasings zuordnen.")
    if le and ab > le:
        raise ZuordnungsFehler(f"Fahrzeug {fahrzeug.kennzeichen}: Die Zuordnung beginnt nach dem Leasing-Ende {datum(le)}.")
    if le and bis is None:
        return le, [f"Die Zuordnung endet mit dem Leasing des Fahrzeugs {fahrzeug.kennzeichen} am {datum(le)}."]
    if le and bis > le:
        raise ZuordnungsFehler(f"Fahrzeug {fahrzeug.kennzeichen}: Die Zuordnung endet nach dem Leasing-Ende "
                               f"{datum(le)} – bitte spätestens bis dahin.")
    return bis, []


def karte_zuordnen(db: Session, karte: Ladekarte, *, ab: date, bis: date | None = None,
                   fahrer_id: int | None = None, fahrzeug_id: int | None = None,
                   heute: date) -> tuple[ZuordnungKarte, list[str]]:
    _karte_pruefen(karte, fahrer_id, fahrzeug_id)
    leasing_hinweise = []
    if fahrzeug_id is not None:  # vor dem Beenden der Vorperiode: offenes Ende beendet sie wie bisher
        if (fz := db.get(Fahrzeug, fahrzeug_id)) is not None:
            _, leasing_hinweise = leasing_pruefen(fz, ab, bis)
    zu_beenden = _pruefen(_karten_perioden(db, karte.id), ab, bis, vorperiode_beenden=True)
    if fahrzeug_id is not None and fz is not None:
        bis, _ = leasing_pruefen(fz, ab, bis)
    hinweise = leasing_hinweise + _beenden(db, zu_beenden, ab, lambda z: z.ziel_name)
    neu = ZuordnungKarte(ladekarte_id=karte.id, fahrer_id=fahrer_id, fahrzeug_id=fahrzeug_id,
                         gueltig_ab=ab, gueltig_bis=bis)
    db.add(neu)
    db.flush()
    hinweise += zusammenfassen(db, ZuordnungKarte, ladekarte_id=karte.id, fahrer_id=fahrer_id, fahrzeug_id=fahrzeug_id)
    return neu, hinweise + _rueckwirkend(ab, heute)


def karten_zuordnung_aendern(db: Session, z: ZuordnungKarte, *, ab: date, bis: date | None,
                             fahrer_id: int | None, fahrzeug_id: int | None, heute: date) -> list[str]:
    _karte_pruefen(z.ladekarte, fahrer_id, fahrzeug_id)
    leasing_hinweise = []
    if fahrzeug_id is not None and (fz := db.get(Fahrzeug, fahrzeug_id)) is not None:
        bis, leasing_hinweise = leasing_pruefen(fz, ab, bis)
    _pruefen(_karten_perioden(db, z.ladekarte_id, ohne_id=z.id), ab, bis, vorperiode_beenden=False)
    rueckwirkend = ab < z.gueltig_ab
    z.gueltig_ab, z.gueltig_bis, z.fahrer_id, z.fahrzeug_id = ab, bis, fahrer_id, fahrzeug_id
    db.flush()
    db.refresh(z)
    hinweise = leasing_hinweise + zusammenfassen(db, ZuordnungKarte, ladekarte_id=z.ladekarte_id, fahrer_id=fahrer_id,
                                                 fahrzeug_id=fahrzeug_id)
    return hinweise + (_rueckwirkend(ab, heute) if rueckwirkend else [])


def karten_zuordnung_am(db: Session, karte_id: int, stichtag: date) -> ZuordnungKarte | None:
    if (speicher := db.info.get(SPEICHER)) is not None:
        return next((z for z in speicher[0].get(karte_id, ()) if _gilt(z, stichtag)), None)
    return db.scalars(select(ZuordnungKarte).where(
        ZuordnungKarte.ladekarte_id == karte_id, gueltig_am(ZuordnungKarte, stichtag))
        .order_by(ZuordnungKarte.id)).first()


# ------------------------------------------------------------------ Fahrer:in → Fahrzeug

def _fahrer_perioden(db: Session, fahrer_id: int, ohne_id: int | None = None):
    q = select(ZuordnungFahrerFahrzeug).where(ZuordnungFahrerFahrzeug.fahrer_id == fahrer_id)
    if ohne_id is not None:
        q = q.where(ZuordnungFahrerFahrzeug.id != ohne_id)
    return db.scalars(q).all()


def fahrer_fahrzeug_zuordnen(db: Session, fahrer: Fahrer, fahrzeug: Fahrzeug, *, ab: date,
                             bis: date | None = None, heute: date
                             ) -> tuple[ZuordnungFahrerFahrzeug, list[str]]:
    zu_beenden = _pruefen(_fahrer_perioden(db, fahrer.id), ab, bis, vorperiode_beenden=True)
    hinweise = _ohne_fahrer_kuerzen(db, fahrzeug.id, ab, bis)
    hinweise += _ohne_dienstwagen_kuerzen(db, fahrer.id, ab, bis)
    hinweise += _beenden(db, zu_beenden, ab, lambda z: f"Fahrzeug {z.fahrzeug.kennzeichen}")
    neu = ZuordnungFahrerFahrzeug(fahrer_id=fahrer.id, fahrzeug_id=fahrzeug.id, gueltig_ab=ab, gueltig_bis=bis)
    db.add(neu)
    db.flush()
    hinweise += zusammenfassen(db, ZuordnungFahrerFahrzeug, fahrer_id=fahrer.id, fahrzeug_id=fahrzeug.id)
    return neu, hinweise + _rueckwirkend(ab, heute)


def bisherige_fahrer_austragen(db: Session, fahrzeug: Fahrzeug, ab: date, ausser_fahrer_id: int
                               ) -> tuple[list[int], list[str]]:
    """Fahrerwechsel am Fahrzeug: alle anderen Fahrer:innen, die das Fahrzeug am Tag `ab` (noch) haben, zum
    Vortag austragen. Liefert (IDs der ausgetragenen Personen, Hinweise). Beginnt eine andere Zuordnung erst
    am oder nach `ab`, kann sie nicht gekürzt werden → Fehler."""
    andere = db.scalars(select(ZuordnungFahrerFahrzeug).where(
        ZuordnungFahrerFahrzeug.fahrzeug_id == fahrzeug.id, ZuordnungFahrerFahrzeug.fahrer_id != ausser_fahrer_id,
        or_(ZuordnungFahrerFahrzeug.gueltig_bis.is_(None), ZuordnungFahrerFahrzeug.gueltig_bis >= ab))).all()
    for z in andere:
        if z.gueltig_ab >= ab:
            raise ZuordnungsFehler(f"{z.fahrer.name} ist diesem Fahrzeug ab {datum(z.gueltig_ab)} zugeordnet – "
                                   "bitte diese Zuordnung zuerst anpassen oder ohne Austragen zuordnen.")
    hinweise = []
    for z in andere:
        z.gueltig_bis = ab - timedelta(days=1)
        hinweise.append(f"{z.fahrer.name} wurde zum {datum(z.gueltig_bis)} aus dem Fahrzeug {fahrzeug.kennzeichen} "
                        "ausgetragen.")
    db.flush()
    return [z.fahrer_id for z in andere], hinweise


def fahrer_fahrzeug_aendern(db: Session, z: ZuordnungFahrerFahrzeug, *, ab: date, bis: date | None,
                            fahrzeug_id: int, heute: date) -> list[str]:
    _pruefen(_fahrer_perioden(db, z.fahrer_id, ohne_id=z.id), ab, bis, vorperiode_beenden=False)
    _ohne_fahrer_pruefen(db, fahrzeug_id, ab, bis)
    _ohne_dienstwagen_pruefen(db, z.fahrer_id, ab, bis)
    rueckwirkend = ab < z.gueltig_ab
    z.gueltig_ab, z.gueltig_bis, z.fahrzeug_id = ab, bis, fahrzeug_id
    db.flush()
    db.refresh(z)
    hinweise = zusammenfassen(db, ZuordnungFahrerFahrzeug, fahrer_id=z.fahrer_id, fahrzeug_id=fahrzeug_id)
    return hinweise + (_rueckwirkend(ab, heute) if rueckwirkend else [])


def fahrzeug_von_fahrer_am(db: Session, fahrer_id: int, stichtag: date) -> Fahrzeug | None:
    if (speicher := db.info.get(SPEICHER)) is not None:
        z = next((z for z in speicher[1].get(fahrer_id, ()) if _gilt(z, stichtag)), None)
    else:
        z = db.scalars(select(ZuordnungFahrerFahrzeug).where(
            ZuordnungFahrerFahrzeug.fahrer_id == fahrer_id, gueltig_am(ZuordnungFahrerFahrzeug, stichtag))
            .order_by(ZuordnungFahrerFahrzeug.id)).first()
    return z.fahrzeug if z else None


def fahrer_von_fahrzeug_am(db: Session, fahrzeug_id: int, stichtag: date) -> list[Fahrer]:
    if (speicher := db.info.get(SPEICHER)) is not None:
        fahrer = {z.fahrer.id: z.fahrer for z in speicher[2].get(fahrzeug_id, ()) if _gilt(z, stichtag)}
        return sorted(fahrer.values(), key=lambda f: (f.name.casefold(), f.name))
    return list(db.scalars(
        select(Fahrer).join(ZuordnungFahrerFahrzeug, ZuordnungFahrerFahrzeug.fahrer_id == Fahrer.id)
        .where(ZuordnungFahrerFahrzeug.fahrzeug_id == fahrzeug_id,
               gueltig_am(ZuordnungFahrerFahrzeug, stichtag))
        .order_by(Fahrer.name)))


# ------------------------------------------------------------------ Fahrzeug ohne Fahrer:in

def _ohne_perioden(db: Session, fahrzeug_id: int, ohne_id: int | None = None):
    q = select(FahrzeugOhneFahrer).where(FahrzeugOhneFahrer.fahrzeug_id == fahrzeug_id)
    if ohne_id is not None:
        q = q.where(FahrzeugOhneFahrer.id != ohne_id)
    return db.scalars(q).all()


def _ohne_fahrer_pruefen(db: Session, fahrzeug_id: int, ab: date, bis: date | None,
                         ohne_id: int | None = None) -> None:
    """Fahrer:in-Zuordnung darf keinen Zeitraum „ohne Fahrer:in“ des Fahrzeugs überschneiden."""
    for o in _ohne_perioden(db, fahrzeug_id, ohne_id):
        if ueberlappt(ab, bis, o.gueltig_ab, o.gueltig_bis):
            raise ZuordnungsFehler(f"Das Fahrzeug ist {zeitraum(o.gueltig_ab, o.gueltig_bis)} als „ohne Fahrer:in“ "
                                   "eingetragen – bitte diesen Zeitraum zuerst anpassen.")


def _ohne_fahrer_kuerzen(db: Session, fahrzeug_id: int, ab: date, bis: date | None) -> list[str]:
    """Neue offene Fahrer:in-Zuordnung ab `ab`: ein offener Zeitraum „ohne Fahrer:in“, der vorher beginnt, endet am
    Vortag (wie ein Fahrerwechsel). Andere Überschneidungen sind ein Fehler."""
    hinweise = []
    for o in _ohne_perioden(db, fahrzeug_id):
        if bis is None and o.gueltig_bis is None and o.gueltig_ab < ab:
            o.gueltig_bis = ab - timedelta(days=1)
            hinweise.append(f"Der Zeitraum „ohne Fahrer:in“ wurde zum {datum(o.gueltig_bis)} beendet.")
    db.flush()
    _ohne_fahrer_pruefen(db, fahrzeug_id, ab, bis)
    return hinweise


def _gegen_fahrer_pruefen(db: Session, fahrzeug_id: int, ab: date, bis: date | None) -> None:
    for z in db.scalars(select(ZuordnungFahrerFahrzeug).where(ZuordnungFahrerFahrzeug.fahrzeug_id == fahrzeug_id)):
        if ueberlappt(ab, bis, z.gueltig_ab, z.gueltig_bis):
            raise ZuordnungsFehler(f"{z.fahrer.name} ist dem Fahrzeug {zeitraum(z.gueltig_ab, z.gueltig_bis)} "
                                   "zugeordnet – bitte austragen (Fahrerwechsel) oder die Zuordnung anpassen.")


def ohne_fahrer_eintragen(db: Session, fahrzeug: Fahrzeug, *, ab: date, bis: date | None = None,
                          bisherige_austragen: bool = False) -> tuple[list[int], list[str]]:
    """Zeitraum „ohne Fahrer:in“ anlegen. `bisherige_austragen`: Fahrer:innen, die das Fahrzeug am Tag `ab` haben,
    zum Vortag austragen. Ein offener früherer Zeitraum „ohne Fahrer:in“ endet am Vortag; angrenzende werden
    zusammengefasst. Liefert (IDs der ausgetragenen Personen, Hinweise)."""
    ausgetragen, hinweise = [], []
    if bisherige_austragen:
        ausgetragen, hinweise = bisherige_fahrer_austragen(db, fahrzeug, ab, ausser_fahrer_id=0)
    _gegen_fahrer_pruefen(db, fahrzeug.id, ab, bis)
    zu_beenden = _pruefen(_ohne_perioden(db, fahrzeug.id), ab, bis, vorperiode_beenden=True)
    hinweise += _beenden(db, zu_beenden, ab, lambda o: "ohne Fahrer:in")
    db.add(FahrzeugOhneFahrer(fahrzeug_id=fahrzeug.id, gueltig_ab=ab, gueltig_bis=bis))
    db.flush()
    hinweise += zusammenfassen(db, FahrzeugOhneFahrer, fahrzeug_id=fahrzeug.id)
    return ausgetragen, hinweise


def ohne_fahrer_aendern(db: Session, o: FahrzeugOhneFahrer, *, ab: date, bis: date | None) -> list[str]:
    _pruefen(_ohne_perioden(db, o.fahrzeug_id, ohne_id=o.id), ab, bis, vorperiode_beenden=False)
    _gegen_fahrer_pruefen(db, o.fahrzeug_id, ab, bis)
    o.gueltig_ab, o.gueltig_bis = ab, bis
    db.flush()
    return zusammenfassen(db, FahrzeugOhneFahrer, fahrzeug_id=o.fahrzeug_id)


def ohne_fahrer_am(db: Session, fahrzeug_id: int, stichtag: date) -> FahrzeugOhneFahrer | None:
    return db.scalars(select(FahrzeugOhneFahrer).where(
        FahrzeugOhneFahrer.fahrzeug_id == fahrzeug_id, gueltig_am(FahrzeugOhneFahrer, stichtag))).first()


# ------------------------------------------------------------------ Fahrer:in ohne Dienstwagen

def _ohne_dw_perioden(db: Session, fahrer_id: int, ohne_id: int | None = None):
    q = select(FahrerOhneFahrzeug).where(FahrerOhneFahrzeug.fahrer_id == fahrer_id)
    if ohne_id is not None:
        q = q.where(FahrerOhneFahrzeug.id != ohne_id)
    return db.scalars(q).all()


def _ohne_dienstwagen_pruefen(db: Session, fahrer_id: int, ab: date, bis: date | None) -> None:
    """Fahrzeug-Zuordnung der Person darf keinen Zeitraum „ohne Dienstwagen“ überschneiden."""
    for o in _ohne_dw_perioden(db, fahrer_id):
        if ueberlappt(ab, bis, o.gueltig_ab, o.gueltig_bis):
            raise ZuordnungsFehler(f"Die Person ist {zeitraum(o.gueltig_ab, o.gueltig_bis)} als „ohne Dienstwagen“ "
                                   "eingetragen – bitte diesen Zeitraum zuerst anpassen.")


def _ohne_dienstwagen_kuerzen(db: Session, fahrer_id: int, ab: date, bis: date | None) -> list[str]:
    """Neue offene Fahrzeug-Zuordnung ab `ab`: ein offener Zeitraum „ohne Dienstwagen“, der vorher beginnt, endet am
    Vortag. Andere Überschneidungen sind ein Fehler."""
    hinweise = []
    for o in _ohne_dw_perioden(db, fahrer_id):
        if bis is None and o.gueltig_bis is None and o.gueltig_ab < ab:
            o.gueltig_bis = ab - timedelta(days=1)
            hinweise.append(f"Der Zeitraum „ohne Dienstwagen“ wurde zum {datum(o.gueltig_bis)} beendet.")
    db.flush()
    _ohne_dienstwagen_pruefen(db, fahrer_id, ab, bis)
    return hinweise


def _gegen_fahrzeuge_pruefen(db: Session, fahrer_id: int, ab: date, bis: date | None) -> None:
    for z in db.scalars(select(ZuordnungFahrerFahrzeug).where(ZuordnungFahrerFahrzeug.fahrer_id == fahrer_id)):
        if ueberlappt(ab, bis, z.gueltig_ab, z.gueltig_bis):
            raise ZuordnungsFehler(f"Die Person hat {zeitraum(z.gueltig_ab, z.gueltig_bis)} das Fahrzeug "
                                   f"{z.fahrzeug.kennzeichen} – bitte die Fahrzeug-Zuordnung zuerst anpassen.")


def _grund(grund: str | None) -> str | None:
    grund = (grund or "").strip()
    if len(grund) > 200:
        raise ZuordnungsFehler("Der Grund darf höchstens 200 Zeichen haben.")
    return grund or None


def ohne_dienstwagen_eintragen(db: Session, fahrer: Fahrer, *, ab: date, bis: date | None = None,
                               grund: str | None = None) -> list[str]:
    """Zeitraum „ohne Dienstwagen“ anlegen; ein offener früherer endet am Vortag, angrenzende mit gleichem Grund werden
    zusammengefasst. Liefert Hinweise."""
    grund = _grund(grund)
    _gegen_fahrzeuge_pruefen(db, fahrer.id, ab, bis)
    zu_beenden = _pruefen(_ohne_dw_perioden(db, fahrer.id), ab, bis, vorperiode_beenden=True)
    hinweise = _beenden(db, zu_beenden, ab, lambda o: "ohne Dienstwagen")
    db.add(FahrerOhneFahrzeug(fahrer_id=fahrer.id, gueltig_ab=ab, gueltig_bis=bis, grund=grund))
    db.flush()
    return hinweise + zusammenfassen(db, FahrerOhneFahrzeug, fahrer_id=fahrer.id, grund=grund)


def ohne_dienstwagen_aendern(db: Session, o: FahrerOhneFahrzeug, *, ab: date, bis: date | None,
                             grund: str | None) -> list[str]:
    grund = _grund(grund)
    _pruefen(_ohne_dw_perioden(db, o.fahrer_id, ohne_id=o.id), ab, bis, vorperiode_beenden=False)
    _gegen_fahrzeuge_pruefen(db, o.fahrer_id, ab, bis)
    o.gueltig_ab, o.gueltig_bis, o.grund = ab, bis, grund
    db.flush()
    return zusammenfassen(db, FahrerOhneFahrzeug, fahrer_id=o.fahrer_id, grund=grund)


def ohne_dienstwagen_am(db: Session, fahrer_id: int, stichtag: date) -> FahrerOhneFahrzeug | None:
    if (speicher := db.info.get(SPEICHER)) is not None:
        return next((o for o in speicher[3].get(fahrer_id, ()) if _gilt(o, stichtag)), None)
    return db.scalars(select(FahrerOhneFahrzeug).where(
        FahrerOhneFahrzeug.fahrer_id == fahrer_id, gueltig_am(FahrerOhneFahrzeug, stichtag))).first()
