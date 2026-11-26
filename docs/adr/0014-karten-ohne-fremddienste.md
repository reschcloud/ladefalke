# 0014 – Verortung und Karten ohne externe Dienste

- **Status:** angenommen (Begründung teilweise geändert durch [0015](0015-internet-nur-laden.md))
- **Entschieden:** 11/2026 (Raumbezug Phasen 1–4), bestätigt 24.11.2026

## Kontext

Ladeorte sollen auf Karten erscheinen. Geokodier-Dienste und Kartenkacheln bekämen dabei Adressen privater Wallboxen
und Bewegungsmuster der Mitarbeitenden. Ursprünglich galt außerdem: Die App geht nie ins Internet.

## Entscheidung

- Verortung nur über ein lokales Verzeichnis: GeoNames-Postleitzahlen (Tabelle `geo_plz`), Reihenfolge PLZ → Ortsname →
  Landesmitte; manuelle PLZ hat Vorrang.
- Karten ohne Kacheln: Leaflet lokal, Grenzen, Relief, Straßen und Orte aus Natural Earth und GeoNames als statische
  Dateien; PDF-Heatmaps aus derselben Grundkarte (Pillow).
- Links zu Google Maps bzw. Apple Maps nur auf Klick, in neuem Tab – die App selbst ruft keinen Dienst auf.

## Verworfene Alternativen

- Nominatim, Google Geocoding – übermitteln Adressen, Nutzungsbedingungen, Ratenlimits.
- OpenStreetMap-Kacheln – jeder Kartenaufruf verrät den Ausschnitt; ohne Internet leer.

## Folgen

Verortung ist nur PLZ-genau. Seit ADR 0015 darf die App das PLZ-Verzeichnis selbst herunterladen; der Grundsatz
„keine Daten an Dritte“ bleibt und trägt diese Entscheidung weiter.
