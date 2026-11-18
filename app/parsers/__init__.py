"""Parser für Laderechnungen – je Format ein Modul mit gemeinsamer Schnittstelle (siehe basis.py).

Die Parser werden beim Start automatisch gefunden: jedes Modul in diesem Paket mit einer (nicht abstrakten)
Unterklasse von `basis.Parser`. Hinzufügen = Modul anlegen, Entfernen = Modul löschen; der Kern kennt keine
konkreten Parser. Welche davon genutzt werden, steuert der MSP (aktiv/deaktiviert, services/parser_verwaltung.py).
"""
import importlib
import inspect
import pkgutil
from pathlib import Path

from app.parsers.basis import Parser


def _entdecken() -> dict[str, Parser]:
    gefunden: dict[str, Parser] = {}
    for info in pkgutil.iter_modules(__path__):
        modul = importlib.import_module(f"{__name__}.{info.name}")
        for klasse in vars(modul).values():
            if (inspect.isclass(klasse) and issubclass(klasse, Parser) and not inspect.isabstract(klasse)
                    and klasse.__module__ == modul.__name__):  # nur im eigenen Modul definierte Klassen
                parser = klasse()
                if parser.key in gefunden:
                    raise RuntimeError(f"Parser-Schlüssel „{parser.key}“ ist doppelt vergeben.")
                gefunden[parser.key] = parser
    return dict(sorted(gefunden.items(), key=lambda e: e[1].name.lower()))


PARSER: dict[str, Parser] = _entdecken()  # alle installierten Parser (aktive siehe services/parser_verwaltung.py)


def passende_parser(pfad: Path) -> list[str]:
    """Schlüssel aller Parser, die die Datei für sich erkennen – jeder Parser entscheidet für sich allein."""
    passend = []
    for parser in PARSER.values():
        try:
            if parser.erkennen(pfad):
                passend.append(parser.key)
        except Exception:  # defekte Datei o. ä. – passt nicht zu diesem Parser
            continue
    return passend


def parser_erkennen(pfad: Path) -> str | None:
    """Schlüssel des Parsers, wenn genau einer passt; sonst None (dann wird gewählt, nicht geraten). So hängt das
    Ergebnis nicht von der Reihenfolge der Parser ab, und kein Parser kann einem anderen Dateien wegnehmen."""
    passend = passende_parser(pfad)
    return passend[0] if len(passend) == 1 else None


def unterstuetzte_formate() -> str:
    return ", ".join(p.name for p in PARSER.values())
