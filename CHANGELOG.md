# Änderungen

Versionen nach `HAUPT.NEBEN.FIX` (siehe README „Betrieb → Release erstellen“). Je Release: was neu ist und was beim
Update von Hand zu tun ist. Updates selbst: README „Betrieb → Update“.

## v1.10.0 – 24.11.2026

### Neu

- **Ohne Dienstwagen:** Hat eine Person in einem Zeitraum bewusst keinen Dienstwagen, aber mit ihrer persönlichen
  Karte geladen, lässt sich das auf ihrer Seite eintragen (Zeitraum, optional Grund) – oder direkt unter „Aufgaben →
  Ladevorgänge ohne Fahrzeug“ mit dem Knopf „ohne Dienstwagen“. Die Ladungen sind dann keine Aufgabe mehr, zählen
  weiter bei der Person und stehen in den Auswertungen als eigener Posten „Ohne Dienstwagen“.
- **Ersatz-/Mietfahrzeug:** Häkchen am Fahrzeug für Autos, die jemand nur vorübergehend fährt. Sie werden normal
  zugeordnet und ausgewertet, lösen aber keine Prüfungen der Dienstwagen aus (Lücken ohne Fahrer:in, Ladekarten je
  Fahrzeug, Vignette, Streckenmaut, Leasing).

### Geändert

- Seitentitel bleiben beim Scrollen oben stehen (bei schmalem Fenster unter der Menüleiste); der Inhalt scrollt
  darunter durch. Sprungziele innerhalb einer Seite landen unter dem Titel statt dahinter.

### Beim Update beachten

- nichts (Migration 0036 läuft beim Start; vorher legt die App eine Sicherung an).

## v1.9.0 – 24.11.2026

### Geändert

- Ladevorgänge-Liste und Export schneller: die Person je Vorgang wird aus einmal geladenen Zuordnungen bestimmt
  statt je Vorgang abgefragt (Zeitraum „Jahr“: 54 statt 2.391 Datenbankabfragen, rund sechsmal so schnell). Die
  Liste selbst wird nicht gecacht – sie merkt sich die Filter für „← Alle …“ und das Blättern.
- Kein Admin-Passwort mehr in `.env`: `setup.sh` legt den ersten Admin nach dem Start per Befehl an und zeigt das
  Passwort einmal an. Ohne Konto zeigt die App „Ersten Admin anlegen“ im Browser. Notfall-Zugang (kein Admin mehr,
  Passwort vergessen): `docker compose exec app python -m werkzeuge.admin [benutzername]`. `ADMIN_USER` und
  `ADMIN_PASSWORD` sind nur noch optional; stehen sie noch in `.env`, weist Einstellungen → System darauf hin.

### Beim Update beachten

- `sh setup.sh` entfernt `ADMIN_USER`/`ADMIN_PASSWORD` aus `.env`, sobald es Konten gibt, und startet die App einmal
  neu. Am Konto ändert sich nichts – es gilt das Passwort, das zuletzt in der App gesetzt wurde. Von Hand: die beiden
  Zeilen löschen und `docker compose up -d`.

## v1.8.0 – 24.11.2026

### Geändert

- Schneller: Die Monatsdiagramme (Dashboard, Zusammenfassung) entstehen im selben Durchlauf wie die Auswertung des
  Zeitraums statt aus einer eigenen Auswertung je Monat (Dashboard „Jahr“ bei gleichen Werten etwa dreimal so
  schnell).
- Seiten-Cache für Dashboard, Auswertungen (Übersicht, Fahrzeug, Fahrer:in, CPO) und Reports: eine berechnete Seite
  wird je Benutzer:in und Adresse gemerkt und sofort wieder ausgeliefert, bis sich Daten ändern (jede gespeicherte
  Änderung macht alle gemerkten Seiten ungültig; höchstens 10 Minuten, nur am selben Tag). Seiten mit Meldungen
  kommen nie aus dem Cache. Von Hand neu rechnen: harter Reload im Browser (Strg+Shift+R bzw. Cmd+Shift+R).

### Beim Update beachten

- nichts. Daten, die von außerhalb der App geändert werden (z. B. Wiederherstellen einer Sicherung per Kommando),
  erscheinen auf gemerkten Seiten spätestens nach 10 Minuten bzw. nach einem Neustart der App.

## v1.7.3 – 24.11.2026

### Behoben

- Dashboard und Auswertungen auf langsamen Servern sehr träge (Zeitraum „Jahr“ bzw. „Alles“, z. B. 15 s auf einem
  Mac mini): je Ladevorgang wurde einzeln nachgefragt, wem die Karte am Tag gehörte und wer das Fahrzeug fuhr –
  bei einem Jahr über 10.000 Datenbankabfragen. Die Zuordnungen werden jetzt je Auswertung einmal geladen
  (Jahr: 762 statt 10.087 Abfragen, rund 2,5× schneller; auf langsamen Servern mehr, weil dort jede Abfrage teurer
  ist).

### Beim Update beachten

- nichts.

## v1.7.2 – 24.11.2026

### Behoben

- `setup.sh`, neue Installation nach einem abgebrochenen Versuch: Lag die Datenbank des ersten Versuchs noch da (Volume
  `ladefalke_pgdata`), aber keine `.env`, entstand ein neues Passwort, mit dem die App nicht in die alte Datenbank kam
  – sie startete immer wieder neu. Jetzt fragt das Skript, ob die alte Datenbank gelöscht werden soll (nie
  automatisch, auch nicht mit `--ja`).
- Startet die App nicht, wartet `setup.sh` nicht mehr 10 Minuten, sondern bricht ab, sobald der Container neu
  startet, und zeigt die Fehlerzeilen – beim falschen Datenbank-Passwort mit Anleitung.
- Servername für das Zertifikat: `setup.sh` prüft die Eingabe (IP-Adresse oder Rechnername) und lässt sie bestätigen;
  die App überspringt ungültige Angaben in `TLS_NAMEN` mit Warnung, statt beim Start abzustürzen.

### Beim Update beachten

- nichts.

## v1.7.1 – 24.11.2026

### Geändert

- `setup.sh` prüft, ob sudo nötig ist: Docker installieren oder den gestoppten Docker-Dienst starten, Zugriff auf
  Docker ohne Gruppe `docker` (dann Compose über sudo). Es sagt vorher, wofür, und fragt das Passwort einmal ab;
  ohne Terminal eine klare Meldung statt einer hängenden Abfrage. Gehört eine bestehende Installation root (früher
  mit sudo eingerichtet), bietet es an, sich mit sudo neu zu starten. Die Ausgabe nennt die Rechte („ohne sudo“,
  „Docker über sudo“, „als root“).

### Beim Update beachten

- nichts.

## v1.7.0 – 24.11.2026

### Neu

- Geodaten lädt die App selbst: die GeoNames-Postleitzahlen für die Verortung der Ladeorte beim ersten Start, nach
  jedem Update und monatlich (download.geonames.org, es werden keine Daten gesendet); danach werden alle Ladeorte neu
  verortet. Ohne Internet gilt `data/geo/allCountries.zip` bzw. `.txt`. Das Einspielen per Befehl entfällt.
- Hinweis auf neue Versionen: die App sieht täglich auf GitHub nach und zeigt neben der Version „Update auf …
  verfügbar“.
- Einstellungen → System: installierte Version, Update-Anleitung, Stand der Geodaten mit „Jetzt aktualisieren“;
  beides abschaltbar.
- Installation mit einem Befehl: `curl -fsSL https://raw.githubusercontent.com/reschcloud/ladefalke/main/setup.sh | sh`
  – installiert bei Bedarf Docker (nach Rückfrage, get.docker.com), legt den Ordner `ladefalke` an, legt sich selbst
  dort ab und wartet, bis die App bereit ist.
- Update: `sh setup.sh` im Installationsordner bietet ein neueres Release an (`--update` geht weiter).

### Beim Update beachten

- nichts. Wer die Postleitzahlen bisher von Hand eingespielt hat: `data/geo` kann bleiben (dient nur noch als
  Rückfall ohne Internet). Nach dem Update lädt die App die Geodaten einmal neu.

## v1.6.1 – 23.11.2026

### Neu

- `setup.sh`: Installation in einem Schritt – ermittelt das neueste Release, holt dessen `compose.yaml` und
  `.env.example`, erzeugt alle Passwörter und Schlüssel zufällig (Datenbank, erster Admin, `SECRET_KEY`,
  `BACKUP_PASSWORT`), fragt den Servernamen für das Zertifikat, legt die Datenordner an und startet. Eine vorhandene
  `.env` bleibt unverändert.
- Update mit `sh setup.sh --update [x.y.z]`: zeigt die Hinweise „Beim Update beachten“ der übersprungenen Versionen,
  sichert und ersetzt `compose.yaml`, ergänzt neue Einträge in `.env`, setzt die Version und startet neu.
- README: Rechnungsformate vollständig (EnBW und Salzburg AG auch als CSV, Shell Recharge); USt.-Hinweis aktuell.

### Geändert

- README: Installation mit Voraussetzungen ganz oben; Entwicklung eigener Abschnitt.
- `.env.example`: Datenbank und Benutzer heißen `ladefalke` (vorher `ladekosten`); bestehende Installationen behalten
  ihre `.env`.

### Beim Update beachten

- nichts (bestehende `.env` nicht ändern – insbesondere `POSTGRES_DB`/`POSTGRES_USER` nicht umbenennen).

## v1.6.0 – 23.11.2026

### Neu

- Benachrichtigung bei Fristen (Mein Konto → „Bei Fristen benachrichtigen“): Mail, sobald eine Frist (Leasing-Ende,
  Führerscheinkontrolle, Vignette, Streckenmaut FLEX) in die Hinweisfrist fällt und noch einmal, wenn sie abläuft –
  mit den betroffenen Fahrzeugen bzw. Personen im Text und dem Report „Offene Aufgaben“ im Anhang. Geprüft einmal
  täglich ab 7 Uhr; je Frist und Stufe eine Mail, ein neues Datum beginnt von vorn. Im Versandprotokoll unter
  „Reports“.
- Spritpreise aus dem EU Weekly Oil Bulletin (Europäische Kommission): Monatsmittel für das Land des Unternehmens
  und den gewählten Kraftstoff (Diesel oder Euro-Super 95) als Spritpreise ab dem 1. des Monats. Automatisch einmal
  pro Woche (abschaltbar; nur ein Download von energy.ec.europa.eu, es werden keine Daten gesendet), auf Knopfdruck oder per
  Hochladen der Datei (Einstellungen → Auswertungen → Gesparte Tankkosten).

### Geändert

- Einstellungen übersichtlicher: Rubriken grob nach dem Menü, einklappbar (Zustand bleibt im Browser), Suche oben
  (blendet Rubriken ohne Treffer aus, markiert passende Felder). Fristen nach Relevanz: überregional zuerst,
  Österreich-Funktionen (Vignette, Streckenmaut FLEX) darunter.
- Stichtag eines Ladevorgangs fest auf Ladebeginn; die Auswahl entfällt.
- Mein Konto: Abos geordnet – Benachrichtigung, Reports nach Zeitplan, Monatsreports.
- Einheitliche Kästen (wie beim Versand in den Einstellungen) für zusammengehörige Optionen: Mein Konto (Theme,
  Zeitraum, Abos), Einstellungen (Plausibilität, kWh-Schätzung, Vorsteuer, Steuersätze, Kennzeichnung,
  Aufbewahrung, Syslog), MSP-Seite (Parser, Vorgaben für Importe). Lange Steuersatz-Liste mit eigenem Bildlauf.
- „Strompreis private Ladungen“ aus den Einstellungen entfernt: Der Preis für Importe ohne Kostenangabe (has.to.be)
  liegt jetzt beim MSP (Stammdaten → MSP), belegt die Import-Vorschau vor und merkt sich den zuletzt verwendeten.
- Ladevorgänge: Ohne Filter geöffnet (z. B. über das Menü) gilt der vorausgewählte Zeitraum aus „Mein Konto“
  statt „Alles“; Links mit Filtern (z. B. von Detailseiten) zeigen ohne Zeitraum weiterhin alles.
- Listen der Stammdaten (MSP, Ladekarten, Fahrzeuge, Fahrer:innen) mit Symbol und Farbe der Art im Titel.

### Behoben

- Aufgaben „Ladevorgänge ohne Fahrzeug“: Je Karte steht die Ursache. Gehört die Karte einer Person, die damals kein
  Fahrzeug hatte, führt „Fahrzeug zuordnen“ zur Person (Zeitraum vorbelegt, danach zurück) statt des irreführenden
  „Karte zuordnen“; das bleibt für Tage, an denen die Karte keine Zuordnung hat. Auch im Report „Offene Aufgaben“.

### Beim Update beachten

- Stichtag: War „Ladeende“ eingestellt, ordnet die App beim ersten Start alle automatisch zugeordneten Ladevorgänge
  nach Ladebeginn neu zu (manuelle bleiben). Monatssummen können danach leicht von den Rechnungen abweichen
  (Ladungen über Monatsgrenzen).
- Migration 0034: Der bisherige „Strompreis private Ladungen“ wird zum Strompreis des has.to.be-MSP (bzw. der zuletzt
  beim Import verwendete Preis).

## v1.5.0 – 23.11.2026

### Geändert

- Meldungen nach Aktionen (z. B. „gespeichert“) als Karten rechts unten über der Seite statt als Banner oben –
  die Seite verschiebt sich nicht mehr. Hinweise blenden sich nach 8 s aus (Maus darüber hält an), Warnungen und
  Fehler bleiben bis „×“.
- Nach dem Speichern (oder einem anderen Formular) bleibt die Seite an ihrer Stelle, statt nach oben zu springen,
  wenn man auf derselben Seite landet.
- Kacheln überall gleich groß (Dashboard, Auswertungen, Detailseiten, PDF-Reports – auch über die Reihen); in allen
  Reports wie in der Flottenbilanz deutlich vom Hintergrund abgesetzt (Fläche und feiner Rahmen).
- Symbol der Ladevorgänge (Ladestation) in den Kacheln pink wie die Heatmap.
- Abhängigkeiten aktualisiert: FastAPI 0.165, Pydantic 2.14.4.

### Beim Update beachten

- nichts

## v1.4.0 – 23.11.2026

### Neu

- Tatsächliche Kosten: Dashboard, Auswertungen, Listen und Reports zeigen netto plus den Teil der USt., der nicht als
  Vorsteuer zurückkommt („Kosten“ statt „Kosten netto“). Vorsteuerabzug je Fahrzeug in Prozent (voll, keiner,
  anteilig z. B. wegen Luxustangente), Standard für Posten ohne Fahrzeug und Land des Unternehmens in den
  Einstellungen; ausländische USt. zählt immer voll. Nennt eine Rechnung keine USt., wird sie mit der neuen
  Steuersatz-Tabelle (Land, gültig ab, Satz – vorbelegt, änderbar) geschätzt und mit ≈ gekennzeichnet. Ladevorgang:
  Kosten mit Netto und USt.; Export mit Netto, USt., Satz und Land.
- Hinweis bei den Kosten (Dashboard, Auswertungen, Zusammenfassung, Fahrer:innen-Report), dass Summen Näherungen sind.
- Import übernimmt die USt. der Rechnung je Posten – auch die bisher ignorierte ausländische (DCS, EnBW, Elli) und
  Reverse Charge (DCS, zählt wie inländische USt.). Bereits importierte Rechnungen werden nach dem Update einmal im
  Hintergrund erneut gelesen und ergänzt; erneut auslösbar unter Einstellungen → Steuersätze.

### Geändert

- Einstellungen → Auswertungen: „Vergleich mit Verbrennern“ (Verbrauch E-PKW und Verbrenner) und zwei Unterabschnitte
  mit Schalter – „CO₂e-Einsparung“ und „Gesparte Tankkosten“ (wie die Führerscheinkontrolle). Spritpreise als Liste
  mit „gültig ab“; jede Ladung nimmt den Preis ihres Tages, Pauschalen den ihres Monats.
- Flottenbilanz: alle Kacheln gleich groß, Zahlen etwas kleiner. CO₂e ab 1.000 kg in ganzen Tonnen (auch in
  Dashboard und Auswertungen).
- Spritpreis und Strompreis privater Ladungen ohne „netto“: einzutragen ist, was tatsächlich gezahlt wird.
- Kennzahlen-Kacheln (Dashboard, Auswertungen, CPO, Detailseiten, PDF-Reports, Flottenbilanz) mit Symbol vor der Zahl:
  Ladestation bei den Ladevorgängen, Geld bei Ø €/kWh und gesparten Tankkosten, Stoppuhr bei der Ladedauer, grünes
  Blatt bei CO₂e (übrige Symbole blau).
- Flottenbilanz: Kacheln deutlicher vom weißen Hintergrund abgesetzt (dunklere Fläche, feiner Rahmen).

### Beim Update beachten

- Migration 0031 (vorher wird gesichert); danach berechnet die App die tatsächlichen Kosten einmal neu. Alle
  Fahrzeuge starten mit 100 % Vorsteuerabzug – Fahrzeuge ohne oder mit anteiligem Abzug auf der Fahrzeugseite
  anpassen; Steuersätze unter Einstellungen → Kosten und USt. prüfen. Der USt.-Nachtrag aus den Originalrechnungen
  läuft nach dem Start im Hintergrund (je nach Menge einige Minuten); bis dahin ist die USt. geschätzt (≈).
- Migration 0032: Ein bisher eingetragener Spritpreis wird zum ersten Eintrag der Liste (gültig ab 1.1.2020), die
  Schalter für CO₂e-Einsparung und Tankkosten sind an, wenn die Werte bisher eingetragen waren.

## v1.3.0 – 21.11.2026

### Neu

- CO₂e-Einsparung gegenüber Verbrennern (grobe Schätzung): in den Einstellungen (Auswertungen) CO₂e des Stroms
  (g/kWh, leer = aus), Verbrauch E-PKW (20 kWh/100 km), Verbrauch Verbrenner (7,4 l/100 km) und Emissionen Verbrenner
  (3,2 kg/l). Eingeschaltet zeigen Dashboard und Auswertungen eine Kachel „CO₂e eingespart“; in der Flottenbilanz
  ersetzt sie die Zahl der Fahrer:innen.
- Eingesparte Tankkosten gegenüber Verbrennern (grobe Schätzung): Spritpreis netto (€/l) in den Einstellungen,
  leer = aus; Verbrauchswerte wie bei CO₂e. Tankkosten eines Verbrenners für dieselbe Strecke minus die tatsächlichen
  Kosten netto, als Kachel „Tankkosten gespart“ in Dashboard und Auswertungen; in der Flottenbilanz statt „Ø kWh je
  Fahrzeug“.
- Vorausgewählter Zeitraum je Benutzer:in (Mein Konto → Darstellung): Monat, Quartal, Halbjahr, Jahr oder Alles –
  gilt für Dashboard, Auswertungen und Reports, wenn kein Zeitraum gewählt ist; jeweils der Zeitraum, der den
  Vormonat enthält. Standard bleibt der Vormonat (Migration 0030).
- Neue Symbole (Material Design Icons, wie in Home Assistant): im Untermenü „Stammdaten“, farbig je Art – Fahrzeug
  blau, Person violett, Ladekarte türkis, MSP orange; Warnung, Erledigt, Frist, E-Mail, Schloss usw. als Symbol statt
  Zeichen, auch in den PDF-Reports.
- Detailseiten von Fahrzeug, Fahrer:in, Ladekarte und MSP tragen das Symbol ihrer Art im Titel; ihre Abschnitte
  (Fahrzeug, Fahrer:innen, Ladekarten, Zuordnungen, Letzte Ladevorgänge) haben Symbol und Linie in der passenden Farbe.

### Geändert

- Kennzahlen-Kacheln in Dashboard und Auswertungen gleichmäßig auf zwei Reihen (bei 7–8 Kacheln vier Spalten,
  schmal zwei), damit Zahlen und Beschriftungen nicht umbrechen.
- Summen in Kacheln (Dashboard, Auswertungen, CPO, Detailseiten, PDF-Reports, Flottenbilanz) ohne Nachkommastellen:
  Kosten, Einsparungen und CO₂e (immer in kg) ganzzahlig; Durchschnitte wie Ø €/kWh unverändert. Einzelne
  Ladevorgänge und Importe zeigen weiter Cent-genau.
- Zeitangaben in Tagen (Fristen, letzter Ladevorgang, Lücken) bei längeren Zeiträumen gröber: ab 30 Tagen in ganzen
  Monaten, ab einem Jahr nur „1 Jahr“ bzw. „über 1 Jahr“.
- „Zuordnungen“ und „Zeitleiste“ sind eine Seite mit den Ansichten Zeitleiste (Standard) und Liste; im Menü
  „Stammdaten“ nur noch ein Eintrag „Zuordnungen“.
- Das Fahrzeug-Symbol ist ein einfaches Auto (statt E-Auto); Fristen unter „Aufgaben“ und im Report zeigen Auto bzw.
  Person.
- Aufgaben und Report „Offene Aufgaben“: Fristen je Art in eigenen Abschnitten (Leasing-Ende,
  Führerscheinkontrolle, Vignette, Streckenmaut FLEX), unter „Aufgaben“ mit eigener Kachel je Art.
- Meldungen stehen immer unter den Kacheln: unter „Aufgaben“ die Fristen, „Ladekarten je Fahrzeug“ und Lücken; auf den
  Seiten von Fahrzeug, Fahrer:in und Ladekarte „Offen: …“.
- Menü: Am Punkt „Aufgaben“ steht statt des roten Punkts die Zahl der offenen Punkte (Tooltip: Aufschlüsselung).
- Report „Zuordnungen am Stichtag“ dichter: je Karte höchstens zwei Zeilen (Name · „über …“, darunter Kennung und
  Vertrag), kleinere Abstände – etwa doppelt so viele Personen je Seite.
- Zuordnungen haben je eine zuständige Seite, auf der Überschneidungen sichtbar sind: Ladekarten werden nur bei der
  Karte zugeordnet, Fahrzeuge nur bei der Person (dort jetzt auch „Fahrerwechsel“). Auf der Fahrzeugseite öffnet
  „Fahrer:in zuordnen →“ die Person mit vorbelegtem Fahrzeug und kehrt danach zurück; „Ohne Fahrer:in“ hat dort ein
  eigenes Formular. Die übrigen Seiten zeigen „bei Person ändern →“ bzw. „bei Karte ändern →“, und die
  Bearbeitungsseite einer Zuordnung zeigt die übrigen Zeiträume derselben Person bzw. Karte.

### Behoben

- Report „Karten und Preise je CPO“: Die Überschrift „CPO mit Sparpotenzial“ und die Kopfzeile eines großen CPO
  standen jeweils allein auf einer Seite, und die Tabelle „MSP je Stromart“ erschien erst am Ende des Blocks. Lange
  CPO-Blöcke stehen jetzt untereinander und laufen über Seiten (Tabellenkopf wiederholt); die Überschrift bleibt beim
  ersten CPO.

### Beim Update beachten

- nichts (Migration 0030 läuft beim Start automatisch, vorher wird gesichert). Die Schätzungen „CO₂e eingespart“
  und „Tankkosten gespart“ sind aus, bis unter Einstellungen → Auswertungen CO₂e des Stroms bzw. ein Spritpreis
  eingetragen ist.

## v1.2.0 – 20.11.2026

### Neu

- Ladekarten je Fahrzeug: Zuordnungen „Karte → Fahrzeug“ nur innerhalb des Leasings (ein offenes Ende wird auf das
  Leasing-Ende begrenzt); bestehende Zuordnungen außerhalb werden zur Aufgabe. Zeiträume, in denen einem Fahrzeug
  weniger oder mehr Karten direkt zugeordnet sind als erlaubt (Einstellungen: an/aus, Mindest- und Höchstzahl, Standard
  genau 1), erscheinen beim Fahrzeug, unter „Aufgaben“ und im Report „Offene Aufgaben“.
- Fahrzeugseite: gemeinsame Zeitleiste mit Fahrer:innen und darunter den Ladekarten, Befunde der Kartenprüfung
  markiert.
- Warnung beim Fahrzeug, wenn Fahrer:innen, Vignette oder Streckenmaut FLEX außerhalb des Leasings liegen
  (offene Fahrer:in-Zuordnung erst nach dem Leasing-Ende; Vignette/Streckenmaut nur komplett außerhalb).
- Ladekarten: Die Liste zeigt bei Fahrzeugkarten neben dem Kennzeichen, wer das Fahrzeug heute fährt, und den letzten
  Ladevorgang je Karte (mit „vor x Tagen“); die Detailseite Kennzahlen (wie bei Fahrzeug und
  Fahrer:in) sowie ersten und letzten Ladevorgang mit Link.

### Behoben

- Fahrer:in: Der Knopf „Heute“ neben „Letzte Führerscheinkontrolle“ sitzt bündig neben dem Datumsfeld.
- Monatssäulen (Dashboard, Reports) über lange Zeiträume: Die Achse zeigt nur noch Quartale bzw. Jahre statt jedes
  Monats (die Texte überlagerten sich); der Tooltip trifft auch bei schmalen Säulen den richtigen Monat.
- README „Betrieb“ durchgehend auf das fertige Image ausgerichtet: Wiederherstellen, Update mit geänderter
  `compose.yaml`, PostgreSQL-Hauptversion nur gemeinsam mit einem Ladefalke-Release (das `pg_dump` im Image muss zur
  Datenbank passen).

### Beim Update beachten

- Die Prüfung der Kartenanzahl ist eingeschaltet (genau 1 Karte je Fahrzeug). Fahrzeuge ohne bzw. mit mehreren
  Fahrzeugkarten erscheinen danach unter „Aufgaben“ – Grenzen oder Abschalten unter Einstellungen → Fristen.

## v1.1.0 – 19.11.2026

### Neu

- Abhängigkeiten werden auf GitHub geprüft: Dependabot schlägt Updates vor (Python-Pakete, Basis-Image, PostgreSQL,
  Actions), der Workflow „Prüfung“ testet jeden Push und Pull Request und sucht wöchentlich mit `pip-audit` nach
  bekannten Sicherheitslücken.
- Lock-Dateien heißen `requirements-lock.txt` und `requirements-dev-lock.txt` (Dependabot erkennt nur `.txt`).
- Ältere Rechnungsformate werden erkannt: Salzburg AG (Layout bis etwa 2024 mit „Belegnummer“) und Elli bzw.
  „Easy Charging“ (Rechnung bis etwa 2023, mit Uhrzeit, EVSE-ID, AC/DC und eindeutiger ID je Ladevorgang;
  „Grundgebühr“ als Gebühr der Karte, auch Rechnungen nur mit Grundgebühr).
- Wien Energie: ältere Rechnungen (bis etwa 2023) mit der Vertragsnummer statt einer Kartennummer nach „Ladekarte:“.

### Geändert

- Lücken ohne Fahrer:in: Mit Leasing-Ende wird der ganze Leasingzeitraum geprüft, auch künftige Zeiträume (z. B. eine
  Zuordnung endet vor dem Leasing) – unter „Aufgaben“, auf der Fahrzeugseite und im Report „Offene Aufgaben“. Ohne
  Leasing-Ende wie bisher bis heute. Aktuelle Lücken nennen ihr Ende, falls bekannt.

### Beim Update beachten

- Nichts.

## v1.0.1 – 18.11.2026

### Neu

- Fertige Images in der GitHub Container Registry (`ghcr.io/reschcloud/ladefalke`): Jeder Release-Tag startet eine
  Pipeline, die erst die Tests ausführt und dann das Image baut. Installation und Update laden das Image, statt es
  auf dem Server zu bauen (README „Betrieb“); selbst bauen geht weiterhin.
- Die Versionsanzeige kommt im Image aus dem Build (kein `.git` mehr nötig).

### Beim Update beachten

- Selbst gebaute Installationen laufen unverändert weiter (`git checkout v1.0.1 && docker compose up -d --build`).
- Umstieg auf das fertige Image: `compose.yaml` von v1.0.1 übernehmen, in `.env` `LADEFALKE_VERSION=1.0.1`
  eintragen, dann `docker compose pull && docker compose up -d`.
- Entwicklung: Das lokal gebaute Image heißt jetzt `ladefalke-app:dev` (`compose.dev.yaml`).

## v1.0.0 – 18.11.2026

Erstes Release.

### Enthalten

- Import der Rechnungen und Ladelisten der unterstützten Anbieter (README „Unterstützte Rechnungsformate“) mit
  Vorschau, Vollständigkeitsprüfung und Pauschalen
- Stammdaten: Fahrzeuge, Fahrer:innen, Ladekarten, MSP, Zuordnungen mit Zeitleiste; Detailseiten mit offenen
  Punkten und Blättern; Zeiträume „ohne Fahrer:in“ (bewusst niemandem zugeordnet, keine Lücke); aneinandergrenzende
  Zuordnungen werden zusammengefasst
- Datumsfelder in Safari (macOS) als Texteingabe TT.MM.JJJJ
- Ladevorgänge mit automatischer Zuordnung zu Fahrzeug und Person, Aufgaben für Offenes
- Auswertungen je Zeitraum, Fahrzeug, Fahrer:in und CPO, Toplisten, Karte, Histogramme zum öffentlichen DC-Laden;
  Export als CSV/XLSX
- PDF-Reports (Zusammenfassung, Fahrer:innen, Karten und Preise je CPO, Flottenbilanz, Zuordnungen, Offene Aufgaben)
  mit Versand per Mail und Zeitplan
- Benutzer:innen mit Rollen, Änderungsprotokoll, Syslog
- Sicherungen (verschlüsselt, Zeitplan, Wiederherstellen per Kommando), automatische Sicherung vor Migrationen
- HTTPS mit selbstsigniertem Zertifikat oder hinter einem Reverse Proxy; Healthcheck `/gesund`

### Beim Update beachten

- Erstinstallation: siehe README „Betrieb → Installation auf einem Server“.
- Bestehende Testinstallationen: `samples/` wird nicht mehr eingebunden und das Image enthält keine Testwerkzeuge
  mehr. Für die Entwicklung in `.env` `COMPOSE_FILE=compose.yaml:compose.dev.yaml` setzen (README „Start“).
