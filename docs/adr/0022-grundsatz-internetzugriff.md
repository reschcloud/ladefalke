# 0022 – Grundsatz Internetzugriff: Browser nur von der App, Server lädt ohne Personenbezug

- **Status:** angenommen
- **Entschieden:** 26.11.2026 (ersetzt den Grundsatz aus [0015](0015-internet-nur-laden.md))

## Kontext

ADR 0015 erlaubte dem Server, öffentliche Daten zu laden, „sendet aber keine Daten“. Das ist zu grob: Jede Anfrage
sendet etwas (mindestens Adresse und Parameter), und für den Browser sagte der Grundsatz nichts. Anlass war die Idee,
Kartenkacheln aus OpenStreetMap zu laden (siehe SPEC.md Abschnitt 11) – deren Koordinaten verraten, welche Gegend
angesehen wird, und hineingezoomt mittelbar Wohnadressen mit privater Wallbox.

## Entscheidung

1. **Browser:** Beim Benutzen der App lädt der Browser nur Inhalte von der App selbst – keine Skripte, Schriften,
   Bilder oder Kartenkacheln von Dritten. Fremde Seiten (z. B. Google Maps) öffnen sich nur auf ausdrücklichen Klick
   in einem neuen Tab.
2. **Server lädt:** Der Server darf öffentliche Daten aus dem Internet laden (Updates, Postleitzahlen, Spritpreise,
   künftig ggf. Kartenkacheln). Eine Anfrage enthält nur, was für den Abruf nötig ist, und lässt keinen Rückschluss
   auf eine bestimmte Person zu: keine Namen, Kennzeichen, Kartennummern, Adressen oder Ladezeitpunkte und keine
   Kennung der Installation oder der Nutzer:innen. Abgerufene Daten werden zwischengespeichert, damit sich aus der
   Häufigkeit der Abrufe nichts ablesen lässt.
3. **Server sendet:** Daten aus der Datenbank verlassen den Server nur über Wege, die ein Admin selbst einrichtet
   (Mail, Syslog, Backup).
4. **Abschaltbar und sichtbar:** Jeder Abruf lässt sich abschalten, die App bleibt ohne Internet voll nutzbar, und
   Einstellungen → System zeigt alle Abrufe mit ihrem letzten Stand.

## Verworfene Alternativen

- Browser darf laden, „solange keine Rückschlüsse über Cookies, IP-Adresse o. Ä. möglich sind“ – im Einzelfall kaum
  zu beurteilen (jeder Abruf bei Dritten verrät die IP-Adresse); „nur von der App selbst“ ist eindeutig und prüfbar.
- Server darf laden, „solange kein direkter Personenbezug entsteht“ – greift zu kurz: Ortsangaben (Kachel in hoher
  Zoomstufe um eine private Wallbox) haben mittelbaren Personenbezug. Deshalb ist festgelegt, was eine Anfrage
  enthalten darf.
- Weiter nur ADR 0015 – sagte nichts zum Browser und war beim „Senden“ ungenau.

## Folgen

- Die bestehenden Abrufe (Geodaten, Oil Bulletin, Update-Hinweis) erfüllen den Grundsatz. Punkt 4 (Übersicht aller
  Abrufe unter Einstellungen → System) ist für Geodaten und Updates umgesetzt; die Spritpreise stehen bei den
  Auswertungs-Einstellungen.
- Punkt 1 gilt heute, ist aber noch nicht technisch erzwungen; eine Content-Security-Policy (`default-src 'self'`)
  würde das sicherstellen und ließe sich testen.
- Kartenkacheln aus OpenStreetMap wären nach Punkt 2 nur über den Server mit Zwischenspeicher zulässig. Die
  Kachelkoordinaten verraten dann, *welche* Gegend angesehen wird, aber nicht *wer*, *warum* oder – dank
  Zwischenspeicher – *wann*.
