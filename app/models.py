"""Datenmodell (SPEC Abschnitt 3).

Überlappungsfreiheit der Zuordnungsperioden wird zusätzlich per
EXCLUDE-Constraint in der Migration 0001 garantiert (nicht im ORM abgebildet).
"""
import enum
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (Boolean, CheckConstraint, Date, DateTime, Enum, ForeignKey, Index, Numeric, String, Text,
                        UniqueConstraint, func, text)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class Ladeart(str, enum.Enum):
    oeffentlich = "oeffentlich"
    privat = "privat"

    @property
    def label(self) -> str:
        return {"oeffentlich": "öffentlich", "privat": "privat"}[self.value]


class KennungTyp(str, enum.Enum):
    vertragsnummer = "vertragsnummer"
    kartennummer = "kartennummer"
    kartenlabel = "kartenlabel"

    @property
    def label(self) -> str:
        return {"vertragsnummer": "Vertragsnummer", "kartennummer": "Kartennummer",
                "kartenlabel": "Kartenbezeichnung"}[self.value]


def _enum(cls, name):
    return Enum(cls, name=name, values_callable=lambda e: [x.value for x in e])


class Msp(Base):
    __tablename__ = "msp"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), unique=True)
    parser_key: Mapped[str | None] = mapped_column(String(50), unique=True)
    standard_ladeart: Mapped[Ladeart] = mapped_column(_enum(Ladeart, "ladeart"))
    stromart_standard: Mapped[str | None] = mapped_column(String(2))  # AC/DC-Vorgabe für Vorgänge ohne Angabe
    cpo_standard: Mapped[str | None] = mapped_column(String(60))  # CPO-Vorgabe (Kennung „AT*XXX“ oder Name)
    # Strompreis je kWh für Importe ohne Kostenangabe (z. B. has.to.be-Wallboxen): Vorbelegung beim Import, danach der
    # zuletzt verwendete Preis (Migration 0034; vorher „Strompreis private Ladungen“ in den Einstellungen)
    strompreis: Mapped[Decimal | None] = mapped_column(Numeric(8, 4))
    # Parser deaktiviert: die App tut, als gäbe es ihn nicht (keine Erkennung, keine Auswahl); Daten bleiben
    parser_aktiv: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    # monatliche Rechnung je Ladekarte (Vollständigkeit erwartet dann je Karte einen Eintrag); Vorbelegung vom Parser
    abrechnung_je_karte: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")

    ladekarten: Mapped[list["Ladekarte"]] = relationship(back_populates="msp")


class Ladekarte(Base):
    __tablename__ = "ladekarte"
    __table_args__ = (UniqueConstraint("msp_id", "kennung", name="uq_ladekarte_msp_kennung"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    msp_id: Mapped[int] = mapped_column(ForeignKey("msp.id", ondelete="RESTRICT"))
    kennung: Mapped[str] = mapped_column(String(200))
    kennung_typ: Mapped[KennungTyp] = mapped_column(_enum(KennungTyp, "kennung_typ"))
    bezeichnung: Mapped[str | None] = mapped_column(String(200))
    # Zusätzlich zur Kennung, z. B. Wien Energie: Gebühren von Verträgen ohne Ladevorgang tragen keine Kartennummer
    vertragsnummer: Mapped[str | None] = mapped_column(String(100), index=True)
    # Gastkarten werden keinem Fahrzeug zugeordnet und in Auswertungen als „Gäste“ geführt
    ist_gastkarte: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    aktiv: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    notiz: Mapped[str | None] = mapped_column(Text)

    msp: Mapped[Msp] = relationship(back_populates="ladekarten")
    zuordnungen: Mapped[list["ZuordnungKarte"]] = relationship(
        back_populates="ladekarte", order_by="ZuordnungKarte.gueltig_ab.desc()")


class Fahrer(Base):
    __tablename__ = "fahrer"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    aktiv: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    fuehrerschein_kontrolle: Mapped[date | None] = mapped_column(Date)  # letzte Kontrolle (services/fristen.py)
    email: Mapped[str | None] = mapped_column(String(254))  # für den Mail-Versand (z. B. eigener Report)


class GeoPlz(Base):
    """Freies Postleitzahlen-Verzeichnis (GeoNames, CC-BY 4.0) für die Geokodierung ohne externe Dienste."""
    __tablename__ = "geo_plz"
    __table_args__ = (Index("ix_geo_plz_land_plz", "land", "plz_norm"), Index("ix_geo_plz_land_name", "land", "name_norm"))

    id: Mapped[int] = mapped_column(primary_key=True)
    land: Mapped[str] = mapped_column(String(2))
    plz: Mapped[str] = mapped_column(String(20))
    plz_norm: Mapped[str] = mapped_column(String(20))  # ohne Leerzeichen, Großbuchstaben
    name: Mapped[str] = mapped_column(String(180))
    name_norm: Mapped[str] = mapped_column(String(180))  # klein, ohne Akzente, „St.“ → „sankt“
    lat: Mapped[Decimal] = mapped_column(Numeric(9, 6))
    lon: Mapped[Decimal] = mapped_column(Numeric(9, 6))


class Ladeort(Base):
    """Adresse eines Ladeorts; mehrere Ladevorgänge teilen sich einen Ort (services/ladeorte.py)."""
    __tablename__ = "ladeort"

    id: Mapped[int] = mapped_column(primary_key=True)
    schluessel: Mapped[str] = mapped_column(String(400), unique=True)  # Land + vereinheitlichte Adresse
    land: Mapped[str | None] = mapped_column(String(2))
    adresse: Mapped[str] = mapped_column(String(300))
    plz: Mapped[str | None] = mapped_column(String(12))  # aus der Adresse erkannt
    ort: Mapped[str | None] = mapped_column(String(120))
    plz_manuell: Mapped[str | None] = mapped_column(String(12))  # vom Benutzer eingetragen, hat Vorrang
    ort_manuell: Mapped[str | None] = mapped_column(String(120))  # vom Benutzer eingetragener Ortsname
    # Koordinaten (Geokodierung, Phase 3); genauigkeit: „manuell“ | „plz“ | „ort“ | „land“ | „verzichtet“
    # („verzichtet“: bewusst ohne Verortung – keine Aufgabe mehr, nicht auf der Karte)
    lat: Mapped[Decimal | None] = mapped_column(Numeric(9, 6))
    lon: Mapped[Decimal | None] = mapped_column(Numeric(9, 6))
    genauigkeit: Mapped[str | None] = mapped_column(String(10))

    @property
    def plz_wirksam(self) -> str | None:
        return self.plz_manuell or self.plz


class Fahrzeug(Base):
    __tablename__ = "fahrzeug"

    id: Mapped[int] = mapped_column(primary_key=True)
    kennzeichen: Mapped[str] = mapped_column(String(30), unique=True)
    modell: Mapped[str | None] = mapped_column(String(200))
    aktiv: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    notiz: Mapped[str | None] = mapped_column(Text)
    leasing_beginn: Mapped[date | None] = mapped_column(Date)
    leasing_ende: Mapped[date | None] = mapped_column(Date)  # inklusive; Ablauf wird überwacht (services/fristen.py)
    # Österreich: Autobahn-Vignette und Streckenmaut FLEX – je Fahrzeug nötig oder nicht, Gültigkeit inklusive;
    # überwacht nur, wenn das Feature in den Einstellungen aktiviert ist (services/fristen.py)
    vignette_noetig: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    vignette_ab: Mapped[date | None] = mapped_column(Date)
    vignette_bis: Mapped[date | None] = mapped_column(Date)
    streckenmaut_noetig: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    streckenmaut_ab: Mapped[date | None] = mapped_column(Date)
    streckenmaut_bis: Mapped[date | None] = mapped_column(Date)
    # Vorsteuerabzug für inländische USt. auf Ladekosten in Prozent (100 = voll, 0 = keiner, anteilig z. B. wegen
    # Luxustangente); bestimmt die tatsächlichen Kosten (services/kosten.py), Migration 0031
    vorsteuer_prozent: Mapped[Decimal] = mapped_column(Numeric(5, 2), default=Decimal(100), server_default="100")
    # Ersatz-/Mietfahrzeug (Migration 0036): wird normal zugeordnet und ausgewertet, ist aber von den Prüfungen der
    # Dienstwagen ausgenommen (Lücken ohne Fahrer:in, Kartenanzahl, Vignette/Streckenmaut, Hinweise)
    ersatzfahrzeug: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    # Grundlage der kWh-Schätzung kommt aus den Daten (services/ladeprofil.py, schaetzgrundlage)


class ZuordnungKarte(Base):
    """Ladekarte → Fahrer:in ODER Fahrzeug für einen Zeitraum (gueltig_bis inklusive, NULL = offen)."""
    __tablename__ = "zuordnung_karte"
    __table_args__ = (
        CheckConstraint("num_nonnulls(fahrer_id, fahrzeug_id) = 1", name="genau_ein_ziel"),
        CheckConstraint("gueltig_bis IS NULL OR gueltig_bis >= gueltig_ab", name="zeitraum"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    ladekarte_id: Mapped[int] = mapped_column(ForeignKey("ladekarte.id", ondelete="RESTRICT"), index=True)
    fahrer_id: Mapped[int | None] = mapped_column(ForeignKey("fahrer.id", ondelete="RESTRICT"))
    fahrzeug_id: Mapped[int | None] = mapped_column(ForeignKey("fahrzeug.id", ondelete="RESTRICT"))
    gueltig_ab: Mapped[date] = mapped_column(Date)
    gueltig_bis: Mapped[date | None] = mapped_column(Date)

    ladekarte: Mapped[Ladekarte] = relationship(back_populates="zuordnungen")
    fahrer: Mapped[Fahrer | None] = relationship()
    fahrzeug: Mapped[Fahrzeug | None] = relationship()

    @property
    def ziel_name(self) -> str:
        if self.fahrer is not None:
            return f"Fahrer:in {self.fahrer.name}"
        return f"Fahrzeug {self.fahrzeug.kennzeichen}" if self.fahrzeug is not None else "–"


class FahrzeugOhneFahrer(Base):
    """Zeitraum, in dem ein Fahrzeug bewusst niemandem zugeordnet ist (Werkstatt, Reserve …) – zählt nicht als Lücke.
    Je Fahrzeug keine Überlappung; darf sich nicht mit einer Fahrer:in-Zuordnung überschneiden (services/zuordnung)."""
    __tablename__ = "fahrzeug_ohne_fahrer"
    __table_args__ = (
        CheckConstraint("gueltig_bis IS NULL OR gueltig_bis >= gueltig_ab", name="zeitraum"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    fahrzeug_id: Mapped[int] = mapped_column(ForeignKey("fahrzeug.id", ondelete="CASCADE"), index=True)
    gueltig_ab: Mapped[date] = mapped_column(Date)
    gueltig_bis: Mapped[date | None] = mapped_column(Date)

    fahrzeug: Mapped[Fahrzeug] = relationship()


class FahrerOhneFahrzeug(Base):
    """Zeitraum, in dem eine Person bewusst keinen Dienstwagen hatte (Migration 0036) – Ladungen mit ihrer
    persönlichen Karte sind dann begründet ohne Fahrzeug: keine Aufgabe, in den Auswertungen „ohne Dienstwagen“.
    Je Person keine Überlappung; darf sich nicht mit einer Fahrzeug-Zuordnung der Person überschneiden."""
    __tablename__ = "fahrer_ohne_fahrzeug"
    __table_args__ = (
        CheckConstraint("gueltig_bis IS NULL OR gueltig_bis >= gueltig_ab", name="zeitraum"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    fahrer_id: Mapped[int] = mapped_column(ForeignKey("fahrer.id", ondelete="CASCADE"), index=True)
    gueltig_ab: Mapped[date] = mapped_column(Date)
    gueltig_bis: Mapped[date | None] = mapped_column(Date)
    grund: Mapped[str | None] = mapped_column(String(200))  # z. B. „noch kein Dienstwagen“, „Mietwagen“

    fahrer: Mapped[Fahrer] = relationship()


class ZuordnungFahrerFahrzeug(Base):
    """Fahrer:in → Fahrzeug für einen Zeitraum. Je Fahrer:in keine Überlappung; Poolfahrzeuge erlaubt."""
    __tablename__ = "zuordnung_fahrer_fahrzeug"
    __table_args__ = (
        CheckConstraint("gueltig_bis IS NULL OR gueltig_bis >= gueltig_ab", name="zeitraum"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    fahrer_id: Mapped[int] = mapped_column(ForeignKey("fahrer.id", ondelete="RESTRICT"), index=True)
    fahrzeug_id: Mapped[int] = mapped_column(ForeignKey("fahrzeug.id", ondelete="RESTRICT"), index=True)
    gueltig_ab: Mapped[date] = mapped_column(Date)
    gueltig_bis: Mapped[date | None] = mapped_column(Date)

    fahrer: Mapped[Fahrer] = relationship()
    fahrzeug: Mapped[Fahrzeug] = relationship()


# ------------------------------------------------------------------ Import (Phase 2)

class ImportStatus(str, enum.Enum):
    vorschau = "vorschau"
    abgeschlossen = "abgeschlossen"


class ZuordnungQuelle(str, enum.Enum):
    automatisch = "automatisch"
    manuell = "manuell"
    keine = "keine"


class Import(Base):
    __tablename__ = "import"
    __table_args__ = (
        # Duplikaterkennung: dieselbe Datei nur einmal abschließend importieren
        Index("uq_import_datei_hash_abgeschlossen", "datei_hash", unique=True,
              postgresql_where=text("status = 'abgeschlossen'")),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    dateiname: Mapped[str] = mapped_column(String(300))
    datei_pfad: Mapped[str] = mapped_column(String(300))  # relativ zum Upload-Verzeichnis
    datei_hash: Mapped[str] = mapped_column(String(64))
    parser_key: Mapped[str | None] = mapped_column(String(50))
    msp_id: Mapped[int | None] = mapped_column(ForeignKey("msp.id", ondelete="RESTRICT"))
    ladeart: Mapped[Ladeart] = mapped_column(_enum(Ladeart, "ladeart"))
    strompreis_netto_kwh: Mapped[Decimal | None] = mapped_column(Numeric(8, 4))
    rechnungsnummer: Mapped[str | None] = mapped_column(String(100))
    rechnungsdatum: Mapped[date | None] = mapped_column(Date)
    abrechnungszeitraum_von: Mapped[date | None] = mapped_column(Date)
    abrechnungszeitraum_bis: Mapped[date | None] = mapped_column(Date)
    rechnungssumme_netto: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    hochgeladen_am: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    importiert_am: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[ImportStatus] = mapped_column(_enum(ImportStatus, "import_status"))
    warnungen: Mapped[list] = mapped_column(JSONB, default=list, server_default="[]")

    msp: Mapped[Msp | None] = relationship()

    @property
    def bezeichnung(self) -> str:
        """Name für Menschen (nie die Datenbanknummer): MSP und Rechnungsnummer, sonst MSP und Abrechnungsmonat,
        sonst der Dateiname."""
        if self.msp is not None and self.rechnungsnummer:
            return f"{self.msp.name} · Rechnung {self.rechnungsnummer}"
        von, bis = self.abrechnungszeitraum_von, self.abrechnungszeitraum_bis
        if self.msp is not None and von:
            monate = f"{von:%m/%Y}" if not bis or (bis.year, bis.month) == (von.year, von.month) else f"{von:%m/%Y}–{bis:%m/%Y}"
            return f"{self.msp.name} · {monate}"
        return self.dateiname

    ladevorgaenge: Mapped[list["Ladevorgang"]] = relationship(
        back_populates="import_", cascade="all, delete-orphan", passive_deletes=True)
    gebuehren: Mapped[list["Gebuehr"]] = relationship(
        back_populates="import_", cascade="all, delete-orphan", passive_deletes=True)


class Ladevorgang(Base):
    __tablename__ = "ladevorgang"
    __table_args__ = (
        Index("uq_ladevorgang_msp_externe_id", "msp_id", "externe_id", unique=True,
              postgresql_where=text("externe_id IS NOT NULL")),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    import_id: Mapped[int] = mapped_column(ForeignKey("import.id", ondelete="CASCADE"), index=True)
    msp_id: Mapped[int] = mapped_column(ForeignKey("msp.id", ondelete="RESTRICT"))
    externe_id: Mapped[str | None] = mapped_column(String(100))
    ladekarte_id: Mapped[int | None] = mapped_column(ForeignKey("ladekarte.id", ondelete="RESTRICT"), index=True)
    roh_kartenkennung: Mapped[str | None] = mapped_column(String(200))
    start: Mapped[datetime] = mapped_column(DateTime)  # lokale Zeit (Europe/Vienna)
    ende: Mapped[datetime] = mapped_column(DateTime, index=True)
    kwh: Mapped[Decimal] = mapped_column(Numeric(10, 3))
    kwh_geschaetzt: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    kosten_netto: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    # USt. laut Rechnung (None = unbekannt, wird aus der Steuersatz-Tabelle geschätzt), Land der USt. (Migration 0031)
    ust_betrag: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    ust_satz: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    ust_land: Mapped[str | None] = mapped_column(String(2))
    # tatsächliche Kosten = netto + nicht abziehbare USt. (services/kosten.py; beim Speichern berechnet)
    kosten: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    standort: Mapped[str | None] = mapped_column(String(300))
    ladepunkt: Mapped[str | None] = mapped_column(String(100))  # wie in der Datei (EVSE-ID oder Wallbox-Nummer)
    evse_id: Mapped[str | None] = mapped_column(String(60))  # normiert, z. B. „AT*MER*E000001*02“ (services/evse.py)
    betreiber_id: Mapped[str | None] = mapped_column(String(60))  # CPO: Kennung „AT*MER“ oder Name (Vorgabe des MSP)
    betreiber_quelle: Mapped[str | None] = mapped_column(String(10))  # rechnung | vorgabe (services/cpo.py)
    land: Mapped[str | None] = mapped_column(String(2), index=True)  # ISO 3166-1 alpha-2
    ladeort_id: Mapped[int | None] = mapped_column(ForeignKey("ladeort.id", ondelete="SET NULL"), index=True)
    ladeart: Mapped[Ladeart] = mapped_column(_enum(Ladeart, "ladeart"))
    stromart: Mapped[str | None] = mapped_column(String(2))  # "AC"/"DC" oder unbekannt
    stromart_quelle: Mapped[str | None] = mapped_column(String(10))  # rechnung | vorgabe | manuell (services/stromart)
    profil_ausgenommen: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    fahrzeug_id: Mapped[int | None] = mapped_column(ForeignKey("fahrzeug.id", ondelete="RESTRICT"), index=True)
    zuordnung_quelle: Mapped[ZuordnungQuelle] = mapped_column(_enum(ZuordnungQuelle, "zuordnung_quelle"))
    rohdaten: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")

    import_: Mapped[Import] = relationship(back_populates="ladevorgaenge")
    msp: Mapped[Msp] = relationship()
    ladekarte: Mapped[Ladekarte | None] = relationship()
    fahrzeug: Mapped[Fahrzeug | None] = relationship()
    ladeort: Mapped[Ladeort | None] = relationship()


class Gebuehr(Base):
    """Rechnungsposition ohne Ladevorgang (Grundgebühr, Roaming-Pauschale …)."""
    __tablename__ = "gebuehr"

    id: Mapped[int] = mapped_column(primary_key=True)
    import_id: Mapped[int] = mapped_column(ForeignKey("import.id", ondelete="CASCADE"), index=True)
    ladekarte_id: Mapped[int | None] = mapped_column(ForeignKey("ladekarte.id", ondelete="RESTRICT"), index=True)
    roh_kartenkennung: Mapped[str | None] = mapped_column(String(200))
    zeitraum_von: Mapped[date | None] = mapped_column(Date)
    zeitraum_bis: Mapped[date | None] = mapped_column(Date)
    bezeichnung: Mapped[str] = mapped_column(String(300))
    betrag_netto: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    ust_betrag: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))  # wie beim Ladevorgang (Migration 0031)
    ust_satz: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    ust_land: Mapped[str | None] = mapped_column(String(2))

    import_: Mapped[Import] = relationship(back_populates="gebuehren")
    ladekarte: Mapped[Ladekarte | None] = relationship()


# ------------------------------------------------------------------ Pauschalen (Phase 3)

class Pauschale(Base):
    """Wallbox mit monatlichem Fixbetrag – keinem Fahrzeug zugeordnet, fließt in die Gesamtwerte ein."""
    __tablename__ = "pauschale"
    __table_args__ = (UniqueConstraint("monat", "bezeichnung", name="uq_pauschale_monat_bezeichnung"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    monat: Mapped[date] = mapped_column(Date, index=True)  # immer der Monatserste
    bezeichnung: Mapped[str] = mapped_column(String(200))
    kwh: Mapped[Decimal] = mapped_column(Numeric(10, 3))
    betrag_netto: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    ladeart: Mapped[Ladeart] = mapped_column(_enum(Ladeart, "ladeart"))
    notiz: Mapped[str | None] = mapped_column(Text)


# ------------------------------------------------------------------ Einstellungen (Phase 4)

class Benutzer(Base):
    """Anmeldung mit Rolle (services/benutzer.py): admin – alles; operator – alles außer Einstellungen, MSP und
    Benutzerverwaltung; viewer – nur lesen."""
    __tablename__ = "benutzer"
    __table_args__ = (CheckConstraint("rolle IN ('admin', 'operator', 'viewer')", name="ck_benutzer_rolle"),
                      CheckConstraint("theme IN ('auto', 'hell', 'dunkel')", name="ck_benutzer_theme"),
                      CheckConstraint("zeitraum IN ('monat', 'quartal', 'halbjahr', 'jahr', 'alles')",
                                      name="ck_benutzer_zeitraum"),
                      Index("ux_benutzer_benutzername", text("lower(benutzername)"), unique=True))

    id: Mapped[int] = mapped_column(primary_key=True)
    benutzername: Mapped[str] = mapped_column(String(80))
    name: Mapped[str] = mapped_column(String(200))
    email: Mapped[str | None] = mapped_column(String(254))  # für den Mail-Versand (Testmail, Reports)
    # Zusammenfassungen per Mail erhalten (automatischer Versand, Vorauswahl beim manuellen)
    zusammenfassung_abo: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    # Report „Offene Aufgaben“ per Mail erhalten (automatischer Versand, Vorauswahl beim manuellen)
    aufgaben_abo: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    # weitere Report-Abos (automatischer Versand, Vorauswahl im Versanddialog), Migration 0028
    karten_cpo_abo: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    flottenbilanz_abo: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    zuordnungen_abo: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    # Benachrichtigung per Mail, wenn Fristen in die Hinweisfrist fallen bzw. ablaufen (services/fristen_mail.py)
    fristen_abo: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    rolle: Mapped[str] = mapped_column(String(10))
    passwort_hash: Mapped[str] = mapped_column(String(300))
    aktiv: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    theme: Mapped[str] = mapped_column(String(10), default="auto", server_default="auto")
    # vorausgewählter Zeitraum (Dashboard, Auswertungen, Reports), Migration 0030
    zeitraum: Mapped[str] = mapped_column(String(10), default="monat", server_default="monat")
    angelegt_am: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    letzte_anmeldung: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Steuersatz(Base):
    """Umsatzsteuer-Normalsatz je Land ab einem Datum (Einstellungen) – für Ladevorgänge und Gebühren, deren Rechnung
    keine USt. nennt (services/kosten.py)."""
    __tablename__ = "steuersatz"
    __table_args__ = (UniqueConstraint("land", "gueltig_ab", name="ux_steuersatz_land_ab"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    land: Mapped[str] = mapped_column(String(2))  # ISO 3166-1 alpha-2
    gueltig_ab: Mapped[date] = mapped_column(Date)
    satz: Mapped[Decimal] = mapped_column(Numeric(5, 2))  # Prozent


class Spritpreis(Base):
    """Spritpreis je Liter ab einem Datum (Einstellungen → Gesparte Tankkosten) – was man tatsächlich zahlen würde."""
    __tablename__ = "spritpreis"

    id: Mapped[int] = mapped_column(primary_key=True)
    gueltig_ab: Mapped[date] = mapped_column(Date, unique=True)
    preis: Mapped[Decimal] = mapped_column(Numeric(6, 3))  # € je Liter
    quelle: Mapped[str] = mapped_column(String(20), default="manuell", server_default="manuell")  # oder oil_bulletin


class Einstellung(Base):
    """Änderbare Einstellungen der Anwendung als Schlüssel/Wert (Standardwerte in services/app_einstellungen.py)."""
    __tablename__ = "einstellung"

    schluessel: Mapped[str] = mapped_column(String(50), primary_key=True)
    wert: Mapped[str] = mapped_column(Text)


class FristMeldung(Base):
    """Gesendete Frist-Benachrichtigung je Benutzer:in (services/fristen_mail.py): je Frist und Stufe („warnung“ =
    Hinweisfrist erreicht bzw. Angabe fehlt, „ablauf“ = abgelaufen) höchstens einmal; ein neues Datum (verlängert)
    beginnt von vorn."""
    __tablename__ = "frist_meldung"

    id: Mapped[int] = mapped_column(primary_key=True)
    benutzer_id: Mapped[int] = mapped_column(ForeignKey("benutzer.id", ondelete="CASCADE"), index=True)
    art: Mapped[str] = mapped_column(String(20))  # leasing | fuehrerschein | vignette | streckenmaut
    objekt: Mapped[str] = mapped_column(String(100))  # Link der Frist (Fahrzeug bzw. Person)
    faellig: Mapped[date | None] = mapped_column(Date)
    stufe: Mapped[str] = mapped_column(String(10))  # warnung | ablauf
    gesendet_am: Mapped[datetime] = mapped_column(DateTime)


class ReportVersand(Base):
    """Versandprotokoll der PDF-Reports (services/versand.py): je Empfänger:in ein Eintrag, auch bei Fehlern."""
    __tablename__ = "report_versand"

    id: Mapped[int] = mapped_column(primary_key=True)
    art: Mapped[str] = mapped_column(String(20))  # fahrer | zusammenfassung | aufgaben | karten_cpo | flottenbilanz | zuordnungen
    zeitraum_art: Mapped[str] = mapped_column(String(10))
    zeitraum_von: Mapped[date] = mapped_column(Date)
    zeitraum_bis: Mapped[date] = mapped_column(Date)
    zeitraum_label: Mapped[str] = mapped_column(String(40))
    fahrer_id: Mapped[int | None] = mapped_column(ForeignKey("fahrer.id", ondelete="SET NULL"))
    benutzer_id: Mapped[int | None] = mapped_column(ForeignKey("benutzer.id", ondelete="SET NULL"))
    empfaenger_name: Mapped[str] = mapped_column(String(200))
    an: Mapped[str] = mapped_column(String(254))
    gesendet_am: Mapped[datetime] = mapped_column(DateTime)
    erfolg: Mapped[bool] = mapped_column(Boolean)
    fehler: Mapped[str | None] = mapped_column(Text)
    ausgeloest: Mapped[str] = mapped_column(String(12))  # manuell | automatisch
    ausgeloest_von: Mapped[int | None] = mapped_column(ForeignKey("benutzer.id", ondelete="SET NULL"))


class Aenderung(Base):
    """Änderungsprotokoll (services/protokoll.py): wer hat was wann geändert; Felder als {Feld: [alt, neu]}
    (Anzeigetexte, sensible Felder ohne Wert)."""
    __tablename__ = "aenderung"

    id: Mapped[int] = mapped_column(primary_key=True)
    zeitpunkt: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    benutzer_id: Mapped[int | None] = mapped_column(ForeignKey("benutzer.id", ondelete="SET NULL"))
    benutzer_name: Mapped[str] = mapped_column(String(200))
    aktion: Mapped[str] = mapped_column(String(30))
    objekt_typ: Mapped[str] = mapped_column(String(30))
    objekt_id: Mapped[int | None] = mapped_column()
    bezeichnung: Mapped[str] = mapped_column(String(300))
    felder: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")
    kontext: Mapped[str | None] = mapped_column(String(300))
