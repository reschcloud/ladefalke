"""Erzeugt app/icons_mdi.py aus dem npm-Paket @mdi/svg (Material Design Icons, Pictogrammers).

Nur die Icons, die app/icons.py verwendet, werden übernommen (Pfad-Daten, viewBox 0 0 24 24).
Lizenz der Icons: Apache 2.0 – app/static/mdi/LICENSE (kopiert aus dem Paket) und LICENSE-Apache-2.0.txt (fest).

Aufruf (Paket vorher entpacken, z. B. `curl -sL https://registry.npmjs.org/@mdi/svg/-/svg-7.4.47.tgz | tar xz`):
    python werkzeuge/mdi_icons.py <pfad/zu/package>
Neue Icons: Namen in app/icons.py (SYMBOLE) eintragen, dann dieses Skript erneut ausführen.
"""
import json
import re
import sys
from pathlib import Path

WURZEL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(WURZEL))

from app.icons import SYMBOLE  # noqa: E402


def main(paket: Path) -> None:
    version = json.loads((paket / "package.json").read_text())["version"]
    pfade = {}
    for mdi_name in sorted(set(SYMBOLE.values())):
        svg = (paket / "svg" / f"{mdi_name}.svg").read_text()
        treffer = re.search(r'<path d="([^"]+)"', svg)
        if not treffer or svg.count("<path") != 1:
            raise SystemExit(f"{mdi_name}: genau ein <path> erwartet")
        pfade[mdi_name] = treffer.group(1)
    zeilen = [
        f'"""Pfad-Daten aus Material Design Icons {version} (@mdi/svg, Pictogrammers).',
        "",
        "Lizenz: Apache 2.0, siehe app/static/mdi/LICENSE. Erzeugt von werkzeuge/mdi_icons.py – nicht von Hand ändern.",
        '"""',
        f'MDI_VERSION = "{version}"',
        "",
        "PFADE = {",
        *(f'    "{n}": "{p}",' for n, p in pfade.items()),
        "}",
        "",
    ]
    (WURZEL / "app" / "icons_mdi.py").write_text("\n".join(zeilen))
    ziel = WURZEL / "app" / "static" / "mdi"
    ziel.mkdir(exist_ok=True)
    (ziel / "LICENSE").write_text((paket / "LICENSE").read_text())
    print(f"{len(pfade)} Icons aus MDI {version} übernommen")


if __name__ == "__main__":
    main(Path(sys.argv[1]))
