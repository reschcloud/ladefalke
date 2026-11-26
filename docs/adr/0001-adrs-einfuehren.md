# 0001 – Entscheidungen als ADRs festhalten

- **Status:** angenommen
- **Entschieden:** 26.11.2026

## Kontext

SPEC.md beschreibt, *wie* die App sich verhält. Die Begründungen standen bisher verstreut darin („entschieden
25.11.2026“, Abschnitt 10 „Geklärte Punkte“) oder nur im Kopf. Änderte sich eine Entscheidung, wurde der Text
überschrieben – die Vorgeschichte blieb höchstens als Halbsatz („Die frühere Regel … gilt nur noch …“).

## Entscheidung

Grundsätzliche Entscheidungen – solche mit echten Alternativen oder einem nicht offensichtlichen Grund – stehen als
Architecture Decision Records in `docs/adr/`, eine Datei je Entscheidung, fortlaufend nummeriert (Vorlage:
[vorlage.md](vorlage.md), Übersicht: [README.md](README.md)).

- SPEC.md beschreibt den aktuellen Stand und verweist auf das ADR („siehe ADR 0010“).
- Ein angenommenes ADR wird inhaltlich nicht mehr geändert. Ändert sich die Entscheidung, entsteht ein neues ADR; das
  alte bekommt den Status „ersetzt durch NNNN“. Tippfehler und Links dürfen korrigiert werden.
- Kleinere Festlegungen zur Bedienung (Knopfbeschriftungen, Reihenfolgen) bleiben in SPEC.md.

## Verworfene Alternativen

- Begründungen weiter in SPEC.md – vermischt Stand und Geschichte, die Spec wird unlesbar lang.
- Nur Commit-Messages – schwer auffindbar; die Historie vor dem 18.11.2026 ist nicht öffentlich (siehe ADR 0021).

## Folgen

Die ADRs 0002–0021 wurden am 26.11.2026 nachträglich aus SPEC.md und dem Code zusammengetragen; das Datum
„Entschieden“ ist dort das ursprüngliche, soweit bekannt.
