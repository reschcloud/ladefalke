"""Symbole der Oberfläche und der PDF-Reports: Material Design Icons (Apache 2.0, app/static/mdi/LICENSE).

Im Template: {{ icon("auto") }} bzw. das Makro m.symbol(name, titel). Die Farbe kommt aus app.css (.symbol-<name>);
Arten (Fahrzeug, Person, Ladekarte, MSP) haben eine eigene Farbe, alles andere nimmt die Textfarbe.
Neues Icon: hier eintragen und werkzeuge/mdi_icons.py ausführen (übernimmt die Pfad-Daten nach app/icons_mdi.py).
"""
from markupsafe import Markup, escape

# eigener Name -> MDI-Name (https://pictogrammers.com/library/mdi/)
SYMBOLE = {
    # Arten
    "auto": "car",
    "person": "account",
    "karte": "credit-card-outline",
    "msp": "lightning-bolt",
    "zuordnung": "link-variant",
    "zeitleiste": "chart-gantt",
    "ladevorgaenge": "ev-station",
    "liste": "format-list-bulleted",
    # Kennzahlen in Kacheln (vor der Zahl)
    "vorgaenge": "ev-station",
    "geld": "cash",
    "dauer": "timer-outline",
    "co2e": "leaf",
    # Zustände
    "warnung": "alert",
    "ok": "check-circle",
    "haken": "check",
    "offen": "clock-outline",
    "info": "information-outline",
    "email": "email-outline",
    "schloss": "lock-outline",
    # Menü (Hauptpunkte bewusst ohne Symbol – zu unruhig; nur Untermenü „Stammdaten“, Menüknopf und Konto)
    "menue": "menu",
    "benutzer": "account-circle-outline",
    # Theme
    "theme-auto": "theme-light-dark",
    "theme-hell": "white-balance-sunny",
    "theme-dunkel": "weather-night",
}

# Grundfarbe als SVG-Attribut „color“ (für die PDF-Reports: WeasyPrint wertet dort kein CSS fürs SVG aus). Im Browser
# hat CSS Vorrang (app.css .symbol-<name>, mit Dark-Mode-Farben). Ohne Eintrag: Textfarbe.
FARBEN = {"auto": "#1c7ed6", "person": "#7950f2", "karte": "#0c8599", "msp": "#e67700", "warnung": "#c98a00",
          "ok": "#1a7f5a", "haken": "#1a7f5a",
          # Kennzahlen: blau, Ladevorgänge pink (wie die Heatmap), CO₂e grün
          "vorgaenge": "#d6336c", "geld": "#1c7ed6", "dauer": "#1c7ed6", "co2e": "#2f9e44"}


def icon(name: str, titel: str = "", klasse: str = "") -> Markup:
    """Inline-SVG (24×24, Füllung in Textfarbe); mit titel als Bild für Screenreader, sonst versteckt."""
    from app.icons_mdi import PFADE

    pfad = PFADE[SYMBOLE[name]]
    klassen = f"symbol symbol-{name}" + (f" {klasse}" if klasse else "")
    if titel:
        t = escape(titel)
        zugang = Markup(f' role="img" aria-label="{t}"><title>{t}</title>')
    else:
        zugang = Markup(' aria-hidden="true">')
    farbe = f' color="{FARBEN[name]}"' if name in FARBEN else ""
    return Markup(f'<svg class="{klassen}" viewBox="0 0 24 24"{farbe} fill="currentColor"{zugang}<path d="{pfad}"/></svg>')
