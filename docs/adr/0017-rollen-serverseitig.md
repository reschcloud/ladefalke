# 0017 – Rollen serverseitig durchsetzen, in der Oberfläche kennzeichnen statt ausblenden

- **Status:** angenommen
- **Entschieden:** 04.11.2026

## Kontext

Drei Rollen: Admin, Operator, Viewer. Ausgeblendete Funktionen verwirren („wo ist der Knopf?“), und eine nur in der
Oberfläche umgesetzte Sperre ist keine.

## Entscheidung

- Durchsetzung auf dem Server: Viewer bekommen bei jeder ändernden Anfrage 403 (außer Abmelden und eigenes Konto);
  Einstellungen und Benutzerverwaltung nur für Admins. Ein Test prüft alle POST-Routen.
- Die Sitzung speichert nur die Benutzer-ID; Rolle und Status gelten bei jeder Anfrage frisch.
- In der Oberfläche bleibt alles sichtbar; was eine Rolle nicht darf, ist ausgegraut bzw. gesperrt mit Grund im
  Tooltip.

## Verworfene Alternativen

- Nicht Erlaubtes ausblenden – Nutzer:innen wissen nicht, dass es die Funktion gibt und wen sie fragen müssen.
- Rolle in der Sitzung zwischenspeichern – Entzug von Rechten wirkt erst nach erneuter Anmeldung.

## Folgen

Jede neue ändernde Route fällt automatisch unter den Test; jede neue Schaltfläche braucht die gesperrte Darstellung.
