"""Symbole (Material Design Icons): jedes verwendete Icon hat Pfad-Daten, Ausgabe für Screenreader."""
from app.icons import SYMBOLE, icon
from app.icons_mdi import PFADE


def test_alle_symbole_haben_pfade():
    # Fehlt eines: werkzeuge/mdi_icons.py erneut ausführen
    assert set(SYMBOLE.values()) <= set(PFADE)


def test_icon_mit_und_ohne_titel():
    ohne = str(icon("auto"))
    assert ohne.startswith('<svg class="symbol symbol-auto" viewBox="0 0 24 24" color="#1c7ed6" fill="currentColor" aria-hidden="true">')
    mit = str(icon("person", 'Person "X"'))
    assert 'role="img" aria-label="Person &#34;X&#34;"><title>Person &#34;X&#34;</title>' in mit

