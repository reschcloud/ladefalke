"""Version der App für die Anzeige (Footer, Reports, Manifest der Sicherungen): Release und Build-Datum.

Release = Git-Tag des laufenden Commits (z. B. „v1.2.0“, siehe README „Betrieb“), sonst der kurze Commit-Stand.
Im Image liegen nur .git/HEAD und die Refs samt Tags (siehe .dockerignore), keine Historie; BUILD_DATUM schreibt das
Dockerfile. Erkannt werden einfache Tags und – über packed-refs – auch annotierte. Fehlt alles (z. B. Start ohne
Git), heißt die Version „unbekannt“.
"""
import re
from datetime import date
from functools import lru_cache
from pathlib import Path

WURZEL = Path(__file__).resolve().parent.parent


def _gepackt(git: Path) -> list[tuple[str, str, str | None]]:
    """packed-refs: (Hash, Ref, Commit hinter einem annotierten Tag oder None)."""
    try:
        zeilen = (git / "packed-refs").read_text().splitlines()
    except OSError:
        return []
    ergebnis = []
    for zeile in zeilen:
        if zeile.startswith("^") and ergebnis:  # „^<commit>“: der Commit des Tags in der Zeile davor
            ergebnis[-1] = (*ergebnis[-1][:2], zeile[1:].strip())
        elif zeile and not zeile.startswith("#") and " " in zeile:
            h, ref = zeile.split(" ", 1)
            ergebnis.append((h, ref.strip(), None))
    return ergebnis


def _commit(git: Path) -> str | None:
    """Voller Hash des laufenden Commits."""
    try:
        kopf = (git / "HEAD").read_text().strip()
    except OSError:
        return None
    if not kopf.startswith("ref: "):
        return kopf or None  # losgelöster HEAD (z. B. git checkout v1.2.0): direkt der Commit
    ref = kopf[5:]
    try:
        return (git / ref).read_text().strip()
    except OSError:
        pass
    return next((h for h, r, _ in _gepackt(git) if r == ref), None)  # Refs können zusammengefasst sein


def _versionsschluessel(tag: str) -> tuple:
    return tuple(int(x) for x in re.findall(r"\d+", tag))


def _tag(git: Path, commit: str) -> str | None:
    """Höchster Versions-Tag (vX.Y.Z) auf diesem Commit."""
    tags = set()
    ordner = git / "refs" / "tags"
    for datei in ordner.rglob("*") if ordner.is_dir() else []:
        try:
            if datei.is_file() and datei.read_text().strip() == commit:
                tags.add(datei.relative_to(ordner).as_posix())
        except OSError:
            pass
    tags |= {r.removeprefix("refs/tags/") for h, r, ziel in _gepackt(git)
             if r.startswith("refs/tags/") and commit in (h, ziel)}
    versionen = [t for t in tags if re.fullmatch(r"v\d+(\.\d+)*", t)]
    return max(versionen, key=_versionsschluessel) if versionen else None


@lru_cache
def version() -> str:
    """z. B. „v1.2.0 · 18.11.2026“ bzw. ohne Release „e207884 · 12.11.2026“."""
    teile = []
    git = WURZEL / ".git"
    if commit := _commit(git):
        teile.append(_tag(git, commit) or commit[:7])
    try:
        teile.append(date.fromisoformat((WURZEL / "BUILD_DATUM").read_text().strip()).strftime("%d.%m.%Y"))
    except (OSError, ValueError):
        pass
    return " · ".join(teile) or "unbekannt"
