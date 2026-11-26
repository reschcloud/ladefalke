# Architecture Decision Records

Grundsätzliche Entscheidungen mit Begründung und verworfenen Alternativen – eine Datei je Entscheidung (siehe
[0001](0001-adrs-einfuehren.md)). Wie die App sich verhält, beschreibt [SPEC.md](../../SPEC.md); hier steht, *warum*.

Neue Entscheidung: [vorlage.md](vorlage.md) kopieren, nächste Nummer vergeben, unten eintragen. Angenommene ADRs
nicht umschreiben – ändert sich die Entscheidung, ein neues ADR anlegen und das alte auf „ersetzt durch NNNN“ setzen.

| Nr. | Entscheidung | Status | Entschieden |
|---|---|---|---|
| [0001](0001-adrs-einfuehren.md) | Entscheidungen als ADRs festhalten | angenommen | 26.11.2026 |
| [0002](0002-serverseitige-oberflaeche.md) | Serverseitig gerenderte Oberfläche statt Single-Page-App | angenommen | Projektbeginn |
| [0003](0003-decimal-fuer-betraege.md) | Geldbeträge und kWh als Decimal, nie float | angenommen | Projektbeginn |
| [0004](0004-tatsaechliche-kosten.md) | Tatsächliche Kosten statt reiner Nettobeträge | angenommen | 23.11.2026 |
| [0005](0005-parser-als-module.md) | Parser als unabhängige Module, der Kern kennt keine Anbieter | angenommen | Projektbeginn |
| [0006](0006-fahrzeug-beim-import-speichern.md) | Fahrzeug beim Import speichern statt bei jeder Abfrage auflösen | angenommen | Projektbeginn |
| [0007](0007-zuordnungsperioden.md) | Zuordnungsperioden inklusiv, Überlappung per Datenbank verboten | angenommen | Projektbeginn |
| [0008](0008-stichtag-ladebeginn.md) | Stichtag ist fest der Ladebeginn | angenommen | 11/2026 |
| [0009](0009-dubletten-ohne-kwh.md) | Dublettenerkennung ohne kWh | angenommen | 04.11.2026 |
| [0010](0010-kwh-schaetzung-ladeprofil.md) | kWh-Schätzung aus dem Ladeprofil, Obergrenze 95. Perzentil | angenommen | 04.11.2026 |
| [0011](0011-ac-dc-herkunftskette.md) | AC/DC ohne Rechnungsangabe über eine feste Vorrangkette | angenommen | 25.11.2026 |
| [0012](0012-gaeste-und-grundgebuehren.md) | Gäste als eigener Topf, kartenbezogene Grundgebühren aufs Fahrzeug | angenommen | 04.11.2026 |
| [0013](0013-topliste-bayesscher-preis.md) | Topliste nach Fahrer:innen mit kWh-gewichtetem Preis | angenommen | 05.11.2026 |
| [0014](0014-karten-ohne-fremddienste.md) | Verortung und Karten ohne externe Dienste | angenommen | 11/2026 |
| [0015](0015-internet-nur-laden.md) | Die App darf aus dem Internet laden, aber keine Daten senden | angenommen | 24.11.2026 |
| [0016](0016-erster-admin-ohne-env.md) | Erster Admin ohne dauerhaftes Passwort in `.env` | angenommen | 24.11.2026 |
| [0017](0017-rollen-serverseitig.md) | Rollen serverseitig durchsetzen, in der Oberfläche kennzeichnen | angenommen | 04.11.2026 |
| [0018](0018-aenderungsprotokoll-after-flush.md) | Änderungsprotokoll zentral beim Speichern | angenommen | 11/2026 |
| [0019](0019-ohne-dienstwagen.md) | „Ohne Dienstwagen“ als Zeitraum, Ersatzfahrzeuge als echte Fahrzeuge | angenommen | 24.11.2026 |
| [0020](0020-import-zurueckstellen.md) | Import-Vorschauen zurückstellen statt verwerfen | angenommen | 25.11.2026 |
| [0021](0021-oeffentliches-repo-ohne-echte-daten.md) | Öffentliches Repository ohne echte Daten | angenommen | 18.11.2026 |
