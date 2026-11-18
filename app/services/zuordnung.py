"""Zeitlich versionierte Zuordnungen (SPEC Abschnitt 5).

Perioden sind Datumsbereiche mit inklusivem Ende; `gueltig_bis = None` bedeutet offen.
Die Datenbank verhindert Überlappungen per EXCLUDE-Constraint – hier wird vorher
geprüft, um verständliche Fehlermeldungen zu liefern.

Aneinandergrenzende Perioden mit gleichem Inhalt (dieselbe Person am selben Fahrzeug, dieselbe Karte am selben Ziel,
„ohne Fahrer:in“ am selben Fahrzeug) werden beim Speichern zu einer zusammengefasst (`zusammenfassen`).
Ein Fahrzeug kann Zeiträume „ohne Fahrer:in“ haben (FahrzeugOhneFahrer): bewusst niemandem zugeordnet, daher keine
Lücke. Sie dürfen sich nicht mit Fahrer:in-Zuordnungen des Fahrzeugs überschneiden; ein offener Zeitraum „ohne
Fahrer:in“ endet automatisch, wenn das Fahrzeug ab einem späteren Tag offen jemandem zugeordnet wird.
"""
from datetime import date, timedelta

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from app.formatierung import datum, zeitraum
from app.models import Fahrer, Fahrzeug, FahrzeugOhneFahrer, Ladekarte, ZuordnungFahrerFahrzeug, ZuordnungKarte


OHNE_FAHRER = "ohne"  # Auswahlwert „– ohne Fahrer:in –“ im Formular „Neue Fahrer:in-Zuordnung“


class ZuordnungsFehler(ValueError):
    pass


def ueberlappt(ab1: date, bis1: date | None, ab2: date, bis2: date | None) -> bool:
    return ab1 <= (bis2 or date.max) and ab2 <= (bis1 or date.max)


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


def karte_zuordnen(db: Session, karte: Ladekarte, *, ab: date, bis: date | None = None,
                   fahrer_id: int | None = None, fahrzeug_id: int | None = None,
                   heute: date) -> tuple[ZuordnungKarte, list[str]]:
    _karte_pruefen(karte, fahrer_id, fahrzeug_id)
    zu_beenden = _pruefen(_karten_perioden(db, karte.id), ab, bis, vorperiode_beenden=True)
    hinweise = _beenden(db, zu_beenden, ab, lambda z: z.ziel_name)
    neu = ZuordnungKarte(ladekarte_id=karte.id, fahrer_id=fahrer_id, fahrzeug_id=fahrzeug_id,
                         gueltig_ab=ab, gueltig_bis=bis)
    db.add(neu)
    db.flush()
    hinweise += zusammenfassen(db, ZuordnungKarte, ladekarte_id=karte.id, fahrer_id=fahrer_id, fahrzeug_id=fahrzeug_id)
    return neu, hinweise + _rueckwirkend(ab, heute)


def karten_zuordnung_aendern(db: Session, z: ZuordnungKarte, *, ab: date, bis: date | None,
                             fahrer_id: int | None, fahrzeug_id: int | None, heute: date) -> list[str]:
    _karte_pruefen(z.ladekarte, fahrer_id, fahrzeug_id)
    _pruefen(_karten_perioden(db, z.ladekarte_id, ohne_id=z.id), ab, bis, vorperiode_beenden=False)
    rueckwirkend = ab < z.gueltig_ab
    z.gueltig_ab, z.gueltig_bis, z.fahrer_id, z.fahrzeug_id = ab, bis, fahrer_id, fahrzeug_id
    db.flush()
    db.refresh(z)
    hinweise = zusammenfassen(db, ZuordnungKarte, ladekarte_id=z.ladekarte_id, fahrer_id=fahrer_id,
                              fahrzeug_id=fahrzeug_id)
    return hinweise + (_rueckwirkend(ab, heute) if rueckwirkend else [])


def karten_zuordnung_am(db: Session, karte_id: int, stichtag: date) -> ZuordnungKarte | None:
    return db.scalars(select(ZuordnungKarte).where(
        ZuordnungKarte.ladekarte_id == karte_id, gueltig_am(ZuordnungKarte, stichtag))).first()


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
    rueckwirkend = ab < z.gueltig_ab
    z.gueltig_ab, z.gueltig_bis, z.fahrzeug_id = ab, bis, fahrzeug_id
    db.flush()
    db.refresh(z)
    hinweise = zusammenfassen(db, ZuordnungFahrerFahrzeug, fahrer_id=z.fahrer_id, fahrzeug_id=fahrzeug_id)
    return hinweise + (_rueckwirkend(ab, heute) if rueckwirkend else [])


def fahrzeug_von_fahrer_am(db: Session, fahrer_id: int, stichtag: date) -> Fahrzeug | None:
    z = db.scalars(select(ZuordnungFahrerFahrzeug).where(
        ZuordnungFahrerFahrzeug.fahrer_id == fahrer_id, gueltig_am(ZuordnungFahrerFahrzeug, stichtag))).first()
    return z.fahrzeug if z else None


def fahrer_von_fahrzeug_am(db: Session, fahrzeug_id: int, stichtag: date) -> list[Fahrer]:
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
