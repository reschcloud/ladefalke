// Karten (Leaflet, alles lokal, keine Kartenkacheln), zwei Modi über data-modus:
// - „statisch“ (Dashboard): reine Heatmap (Leaflet.heat) der öffentlichen Ladungen, ohne jede Bedienung;
//   Daten [lat, lon, anzahl] je Ladeort.
// - „interaktiv“ (Ladevorgänge): Preisschilder je Ort und Stromart mit Ø €/kWh (kWh-gewichtet), Anzahl als
//   Plakette, ≈ bei geschätzten kWh. Farbe umschaltbar über [data-kartenfarbe]: nach Stromart (AC violett, DC pink,
//   ohne Angabe grau) oder nach Preis (grün unter, rot über dem Ø derselben Stromart aller Ladungen auf der Karte). Hat ein Ort mehrere Stromarten, zeigen die Schilder von links und rechts auf den Ort.
//   Dicht liegende Orte werden zu Kreisen (Ring = Anteil der Stromarten), Klick zoomt hinein. Tooltip mit den
//   einzelnen Ladungen (je Zeile ein Link zur Detailseite) – öffnet beim Überfahren, schließt erst verzögert und
//   nicht, solange die Maus über dem Tooltip ist; Klick auf das Schild lässt ihn offen (bis Klick in die Karte).
//   Daten je Ladung: [id, lat, lon, land, ort, stromart, kwh, kosten, geschätzt, datum, cpo, person, auto, dauer_min].
//   Dritte Ansicht „Heatmap“ (wie im Dashboard, aber über die gefilterten Ladungen). Home-Knopf unter den
//   Zoomknöpfen: zurück zur Ausgangsansicht (alle Ladungen der Filter im Blick).
//   Beim Verschieben/Zoomen wird die Liste (#liste) für den Ausschnitt nachgeladen (Parameter „bereich“).
// Hintergrund: Länder, Regionen und Beschriftungen aus /static/geo/basiskarte.js (Natural Earth, window.BASISKARTE).
// Farben aus CSS-Variablen, damit hell/dunkel stimmen. Läuft beim Laden, nach jedem htmx-Seitenwechsel (hx-boost) und
// nach „Zurück“ im Browser (htmx stellt dann nur das HTML wieder her – die Karte muss neu aufgebaut werden).
(function () {
  let aktiv = null;
  const FARBE = "karteFarbe";  // localStorage: Farbgebung der Schilder („art“ oder „preis“)

  function farbe(el, name) { return getComputedStyle(el).getPropertyValue(name).trim(); }

  function beschriftung(text, klasse) {
    const span = document.createElement("span");
    span.textContent = text;
    return L.divIcon({ className: klasse, html: span.outerHTML, iconSize: null });
  }

  function beschriftungen(karte, basiskarte) {
    karte.createPane("beschriftung").style.zIndex = 450;  // über der Heatmap, unter Tooltips
    const marker = (lat, lon, name, klasse) => L.marker([lat, lon], {
      icon: beschriftung(name, klasse), pane: "beschriftung", interactive: false, keyboard: false });
    // Reihenfolge = Vorrang: Ländernamen, dann Städte nach Wichtigkeit (Natural-Earth-Zoomstufe)
    const eintraege = [
      ...basiskarte.laendernamen.map(([lat, lon, name, ab]) =>
        ({ ab, name, land: true, m: marker(lat, lon, name, "karte-landname") })),
      ...basiskarte.orte.map(([lat, lon, name, ab]) => ({ ab, name, land: false, m: marker(lat, lon, name, "karte-stadt") })),
    ];
    // geschätzte Textbox in Pixeln (Schrift .72rem): Länder zentriert, Städte rechts vom Punkt, bei Platzmangel links
    function box(e, links) {
      const p = karte.latLngToContainerPoint(e.m.getLatLng()), breite = e.name.length * (e.land ? 8.5 : 6.5) + 6;
      if (e.land) return [p.x - breite / 2, p.y - 9, p.x + breite / 2, p.y + 9];
      return links ? [p.x - breite - 4, p.y - 9, p.x + 6, p.y + 9] : [p.x - 6, p.y - 9, p.x + breite + 4, p.y + 9];
    }
    function aktualisieren() {
      const zoom = karte.getZoom(), groesse = karte.getSize(), rahmen = karte.getContainer().getBoundingClientRect();
      // Zoomknöpfe und Quellenangabe gelten als belegt
      const belegt = [...karte.getContainer().querySelectorAll(".leaflet-control")].map((c) => {
        const r = c.getBoundingClientRect();
        return [r.left - rahmen.left, r.top - rahmen.top, r.right - rahmen.left, r.bottom - rahmen.top];
      });
      for (const e of eintraege) {
        const frei = (b) => b[0] >= 0 && b[1] >= 0 && b[2] <= groesse.x && b[3] <= groesse.y
          && !belegt.some((o) => b[0] < o[2] && o[0] < b[2] && b[1] < o[3] && o[1] < b[3]);
        let links = false, sichtbar = false;
        if (zoom >= e.ab) {
          let b = box(e, false);
          if (!frei(b) && !e.land) { b = box(e, true); links = true; }
          sichtbar = frei(b);
          if (sichtbar) belegt.push(b);
        }
        if (sichtbar !== karte.hasLayer(e.m)) sichtbar ? e.m.addTo(karte) : e.m.remove();
        if (sichtbar) e.m.getElement().classList.toggle("links", links);
      }
    }
    karte.on("moveend", aktualisieren);
    aktualisieren();
  }

  const svg = (pfad) => `<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.8"
    stroke-linecap="round" aria-hidden="true"><path d="${pfad}"/></svg>`;
  const SYMBOL_GROSS = svg("M2 6V2h4M10 2h4v4M14 10v4h-4M6 14H2v-4");
  const SYMBOL_KLEIN = svg("M6 2v4H2M14 6h-4V2M10 14v-4h4M2 10h4v4");

  const SYMBOL_HOME = svg("M2 8l6-5.5L14 8M4 6.5V14h3v-4h2v4h3V6.5");

  // Home-Knopf (unter den Zoomknöpfen): zurück zur Ausgangsansicht – alle Ladungen der Filter im Blick
  function homeKnopf(karte, ausgangsansicht) {
    const Knopf = L.Control.extend({
      options: { position: "topleft" },
      onAdd() {
        const bar = L.DomUtil.create("div", "leaflet-bar");
        const a = L.DomUtil.create("a", "karte-home-knopf", bar);
        a.href = "#";
        a.setAttribute("role", "button");
        a.title = "Ausgangsansicht (alle Ladungen der Filter)";
        a.setAttribute("aria-label", a.title);
        a.innerHTML = SYMBOL_HOME;
        L.DomEvent.on(a, "click", (e) => { L.DomEvent.preventDefault(e); ausgangsansicht(); });
        L.DomEvent.disableClickPropagation(bar);
        return bar;
      },
    });
    new Knopf().addTo(karte);
  }

  // Knopf „Karte groß anzeigen“ (unter den Zoomknöpfen); Esc schließt
  // Karte erst nach einem Klick bedienbar: vorher scrollt das Mausrad die Seite (man bleibt nicht in der Karte hängen),
  // Ziehen und Zwei-Finger-Zoom sind aus. Der Klick richtet die Karte am oberen Bildschirmrand aus (unter der
  // Menüleiste); ein Klick außerhalb oder Esc sperrt sie wieder. Groß angezeigt ist sie immer bedienbar.
  function erstNachKlick(karte, el) {
    const hinweis = L.DomUtil.create("div", "karte-sperre-hinweis", el);
    hinweis.textContent = "Zum Verschieben und Zoomen in die Karte klicken";
    let aktivIst = false, hinweisZeit = null;
    const setzen = (an) => {
      aktivIst = an;
      el.classList.toggle("karte-gesperrt", !an);
      for (const h of [karte.scrollWheelZoom, karte.dragging, karte.touchZoom, karte.doubleClickZoom]) {
        if (h) an ? h.enable() : h.disable();
      }
    };
    setzen(false);
    el.addEventListener("pointerdown", () => {
      if (aktivIst) return;
      setzen(true);
      hinweis.classList.remove("sichtbar");
      if (el.classList.contains("karte-gross")) return;
      // Menüleiste oben: darunter ausrichten; als Seitenleiste links (breite Fenster) liegt sie nicht darüber
      const seitenleiste = document.body.classList.contains("menue-links") && window.matchMedia("(min-width: 1024px)").matches;
      const kopf = seitenleiste ? 0 : document.querySelector("body > header")?.offsetHeight || 0;
      const ziel = el.closest(".karte-bereich") || el;
      window.scrollTo({ top: ziel.getBoundingClientRect().top + window.scrollY - kopf - 8, behavior: "smooth" });
    }, true);
    // Mausrad über der gesperrten Karte: Seite scrollt weiter, kurz den Hinweis zeigen
    el.addEventListener("wheel", () => {
      if (aktivIst) return;
      hinweis.classList.add("sichtbar");
      clearTimeout(hinweisZeit);
      hinweisZeit = setTimeout(() => hinweis.classList.remove("sichtbar"), 1500);
    }, { passive: true });
    document.addEventListener("pointerdown", (e) => {
      if (aktivIst && el.isConnected && !el.contains(e.target) && !el.classList.contains("karte-gross")) setzen(false);
    });
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape" && aktivIst && el.isConnected && !el.classList.contains("karte-gross")) setzen(false);
    });
  }

  function grossKnopf(karte, el) {
    let setzenAussen = null;
    const Knopf = L.Control.extend({
      options: { position: "topleft" },
      onAdd() {
        const bar = L.DomUtil.create("div", "leaflet-bar");
        const a = L.DomUtil.create("a", "karte-gross-knopf", bar);
        a.href = "#";
        a.setAttribute("role", "button");
        const setzen = (gross) => {
          el.classList.toggle("karte-gross", gross);
          a.innerHTML = gross ? SYMBOL_KLEIN : SYMBOL_GROSS;
          a.title = gross ? "Verkleinern (Esc)" : "Karte groß anzeigen";
          a.setAttribute("aria-label", a.title);
          karte.invalidateSize();
        };
        setzen(false);
        setzenAussen = setzen;
        L.DomEvent.on(a, "click", (e) => { L.DomEvent.preventDefault(e); setzen(!el.classList.contains("karte-gross")); });
        L.DomEvent.disableClickPropagation(bar);
        document.addEventListener("keydown", (e) => {
          if (e.key === "Escape" && el.isConnected && el.classList.contains("karte-gross")) setzen(false);
        });
        return bar;
      },
    });
    new Knopf().addTo(karte);
    return setzenAussen;
  }

  // Landesflagge als Emoji aus dem ISO-Code („AT“ → Regional Indicator A + T), wie formatierung.flagge
  function flagge(land) {
    if (!/^[A-Z]{2}$/.test(land || "")) return "";
    return String.fromCodePoint(...[...land].map((z) => 0x1F1E6 + z.charCodeAt(0) - 65)) + " ";
  }

  // ---------------------------------------------------------------- Details: Relief, Gewässer, Straßen
  // /static/geo/details.json und relief.jpg (werkzeuge/kartendetails.py) – nur auf Seiten mit Karte nachgeladen.
  // Jedes Objekt hat eine Zoomstufe, ab der es erscheint; gezeichnet auf Canvas (schnell bei vielen Linien).
  const ZOOM_VERSATZ = 1.5;  // Natural Earth ist für größere Karten gedacht – kleinere Straßen erst später zeigen
  let detailsLaden = null;
  function kartendetails(karte, el) {
    const version = document.querySelector('meta[name="static-version"]')?.content || "";
    detailsLaden = detailsLaden || fetch(`/static/geo/details.json?v=${version}`).then((a) => a.json());
    detailsLaden.then((d) => {
      if (!karte.getContainer().isConnected) return;
      L.imageOverlay(`/static/geo/${d.relief.bild}?v=${version}`, d.relief.grenzen,
        { pane: "relief", interactive: false, className: "karte-relief" }).addTo(karte);
      const wasser = L.canvas({ pane: "gewaesser" }), strassen = L.canvas({ pane: "strassen" });
      const f = (n) => farbe(el, n), ll = (k) => k.map(([x, y]) => [y, x]);
      const stufen = [];  // [ab_zoom, Layer]
      for (const [ab, k] of d.seen) {
        stufen.push([ab, L.polygon(ll(k), { renderer: wasser, interactive: false, stroke: false,
          fillColor: f("--karte-see"), fillOpacity: 1 })]);
      }
      for (const [ab, breite, k] of d.fluesse) {
        stufen.push([ab + 0.5, L.polyline(ll(k), { renderer: wasser, interactive: false, color: f("--karte-fluss"),
          weight: [0, 0.8, 1.2, 1.8][breite], lineCap: "round" })]);
      }
      const STIL = { autobahn: ["--karte-autobahn", 2, 0], hauptstrasse: ["--karte-hauptstrasse", 1.3, 0.5],
                     strasse: ["--karte-strasse", 0.9, ZOOM_VERSATZ] };
      for (const [art, ab, k] of d.strassen) {
        const [farbname, weight, versatz] = STIL[art];
        stufen.push([ab + versatz, L.polyline(ll(k), { renderer: strassen, interactive: false, color: f(farbname),
          weight, lineCap: "round", lineJoin: "round" })]);
      }
      // Relief nach Zoom abschwächen – nah betrachtet ist es unscharf und soll Straßen und Orte nicht überdecken
      const reliefPane = karte.getPane("relief");
      const zeigen = () => {
        const zoom = karte.getZoom();
        reliefPane.style.opacity = String(Math.max(0.4, Math.min(1, 1 - (zoom - 8) * 0.2)));
        for (const [ab, layer] of stufen) {
          const sichtbar = zoom >= ab;
          if (sichtbar !== karte.hasLayer(layer)) sichtbar ? layer.addTo(karte) : layer.remove();
        }
      };
      karte.on("zoomend", zeigen);
      zeigen();
    }).catch(() => { /* ohne Details: schlichte Basiskarte */ });
  }

  // ---------------------------------------------------------------- Preisschilder (Modus „interaktiv“)
  const ART = ["AC", "DC", null];  // Reihenfolge der Schilder an einem Ort
  const klasse = (art) => (art ? art.toLowerCase() : "ohne");
  const zahl = (x, n) => x.toLocaleString("de-AT", { minimumFractionDigits: n, maximumFractionDigits: n });
  const dauerText = (m) => (m >= 60 ? `${Math.floor(m / 60)}:${String(m % 60).padStart(2, "0")} h` : `${m} min`);
  const summe = (l, i) => l.reduce((s, v) => s + v[i], 0);
  function farbeLesen() {
    try { const m = localStorage.getItem(FARBE); return ["preis", "heat"].includes(m) ? m : "art"; } catch (e) { return "art"; }
  }
  // Heatmap-Ebene (Dashboard und Ansicht „Heatmap“ bei den Ladevorgängen); punkte: [[lat, lon, anzahl], …]
  function heatEbene(el, punkte) {
    const stufen = farbe(el, "--karte-heat").split(",").map((s) => s.trim());
    const gradient = {};
    stufen.forEach((f, i) => { gradient[(0.15 + 0.85 * i / (stufen.length - 1)).toFixed(2)] = f; });
    return L.heatLayer(punkte, { radius: 22, blur: 18, max: Math.max(1, ...punkte.map((p) => p[2])), minOpacity: 0.5,
                                 gradient, maxZoom: 0 });  // 0: Intensität unabhängig vom Zoom (sonst ohne Kacheln ∞ → unsichtbar)
  }
  const preis = (liste) => { const kwh = summe(liste, 6); return kwh > 0 ? summe(liste, 7) / kwh : null; };

  // Ø €/kWh je Stromart über alle Ladungen der Karte – Maßstab für die Farbe nach Preis
  function schnitte(ladungen) {
    const s = {};
    for (const art of ART) s[art || ""] = preis(ladungen.filter((v) => (v[5] || null) === art));
    return s;
  }
  // „guenstig“ / „teuer“ gegenüber dem Ø der eigenen Stromart; ohne kWh „ohne“
  function preisKlasse(p, art, schnitt) {
    const s = schnitt[art || ""];
    return p === null || s === null ? "ohne" : p <= s ? "guenstig" : "teuer";
  }

  // Wert im Schild: Ø €/kWh (Kosten / kWh), „≈“ bei geschätzten kWh
  function schildWert(liste) {
    const p = preis(liste);
    return p === null ? "–" : `${liste.some((v) => v[8]) ? "≈" : ""}${zahl(p, 2)}`;
  }

  function schild(liste, art, lage, modus, schnitt) {
    const plakette = liste.length > 1 ? `<span class="schild-anzahl">${liste.length}</span>` : "";
    const farbe = modus === "preis" ? preisKlasse(preis(liste), art, schnitt) : klasse(art);
    const div = document.createElement("div");
    div.className = `schild ${farbe} ${lage}`;
    // nach Preis gefärbt: Stromart klein im Schild, sonst wäre sie nicht mehr zu erkennen
    const einheit = modus === "preis" && art ? `${art} €/kWh` : "€/kWh";
    div.innerHTML = `<span class="schild-wert"></span><span class="schild-einheit">${einheit}</span>${plakette}`;
    div.firstChild.textContent = schildWert(liste);
    return div.outerHTML;
  }

  // Inhalt des Tooltips: Kopf, dann je Ladung eine Zeile (verlinkt)
  function details(liste, art, modus, schnitt, mitLinks) {
    const div = document.createElement("div");
    div.className = "karte-tip";
    const kopf = document.createElement("div");
    const titel = document.createElement("strong");
    titel.textContent = flagge(liste[0][3]) + liste[0][4];
    const marke = document.createElement("span");
    marke.className = `marker stromart-${klasse(art)}`;
    marke.textContent = art || "?";
    if (!art) marke.title = "keine Angabe";
    kopf.append(titel, " ", marke, ` ${liste.length} Ladevorg${liste.length === 1 ? "ang" : "änge"}`);
    const tabelle = document.createElement("table");
    for (const v of mitLinks ? liste : liste.slice(0, 8)) {
      const zweit = `${v[8] ? "≈" : ""}${zahl(v[6], 1)} kWh`;
      const tr = tabelle.insertRow();
      [v[9], v[10], v[11], v[12], zweit, dauerText(v[13])].forEach((text, i) => {
        const td = tr.insertCell();
        if (i >= 4) td.className = "rechts";
        if (mitLinks && i === 0) {
          const a = document.createElement("a");
          a.href = `/ladevorgaenge/${v[0]}`;
          a.textContent = text;
          td.append(a);
        } else {
          td.textContent = text;
        }
      });
      if (mitLinks) tr.addEventListener("click", () => { window.location.href = `/ladevorgaenge/${v[0]}`; });
    }
    div.append(kopf, tabelle);
    if (!mitLinks && liste.length > 8) {
      const mehr = document.createElement("small");
      mehr.textContent = `… ${liste.length - 8} weitere – Klick zeigt alle`;
      div.append(mehr);
    }
    return div;
  }

  // Anteile für den Ring einer Gruppe: nach Stromart bzw. nach Preis (je Ladung gegenüber dem Ø ihrer Stromart)
  function anteile(liste, modus, schnitt) {
    if (modus === "preis") {
      const k = liste.map((v) => preisKlasse(v[6] > 0 ? v[7] / v[6] : null, v[5], schnitt));
      return ["guenstig", "teuer", "ohne"].map((f) => [f, k.filter((x) => x === f).length]);
    }
    return ART.map((a) => [klasse(a), liste.filter((v) => (v[5] || null) === a).length]);
  }

  function gruppenSymbol(liste, modus, schnitt) {
    const n = liste.length, d = Math.round(Math.min(64, 34 + Math.sqrt(n) * 4));
    let winkel = 0;
    const teile = anteile(liste, modus, schnitt).map(([f, z]) => {
      const von = winkel;
      winkel += z / n * 360;
      return `var(--schild-${f}) ${von}deg ${winkel}deg`;
    });
    return `<div class="karte-gruppe" style="width:${d}px;height:${d}px;background:conic-gradient(${teile.join(", ")})">`
      + `<span>${n}</span></div>`;
  }

  const ANTEIL_TEXT = { ac: "AC", dc: "DC", ohne: "?", guenstig: "unter Ø", teuer: "über Ø" };
  function gruppenTooltip(liste, orte, modus, schnitt) {
    const div = document.createElement("div");
    div.className = "karte-tip";
    const kwh = summe(liste, 6);
    const titel = document.createElement("strong");
    titel.textContent = `${liste.length} Ladevorgänge an ${orte} Orten`;
    // Anteile als Marker wie in Liste und Legende (AC, DC, ? bzw. unter Ø, über Ø)
    const teile = document.createElement("div");
    anteile(liste, modus, schnitt).filter(([, z]) => z).forEach(([f, z], i) => {
      const marke = document.createElement("span");
      marke.className = modus === "preis" ? `schild-muster ${f}` : `marker stromart-${f}`;
      marke.textContent = ANTEIL_TEXT[f];
      if (f === "ohne") marke.title = "keine Angabe";
      teile.append(i ? " · " : "", marke, ` ${z}`);
    });
    const summen = document.createElement("div");
    summen.textContent = `${zahl(kwh, 0)} kWh · Ø ${kwh > 0 ? zahl(summe(liste, 7) / kwh, 2) : "–"} €/kWh`;
    div.append(titel, teile, summen);
    return div;
  }

  // Tooltip im oberen Kartenteil nach unten öffnen, sonst schneidet der Kartenrand ihn ab
  // Geöffneten Tooltip in die Karte schieben, wenn er übersteht (Leaflet verschiebt Tooltips nicht selbst);
  // der Pfeil zeigt dann nicht mehr auf das Symbol und wird ausgeblendet
  function einpassen(karte, tooltip) {
    const el = tooltip.getElement();
    if (!el) return;
    el.classList.remove("verschoben");
    const k = karte.getContainer().getBoundingClientRect(), r = el.getBoundingClientRect(), rand = 6;
    let dx = 0, dy = 0;
    if (r.right > k.right - rand) dx = k.right - rand - r.right;
    if (r.left + dx < k.left + rand) dx = k.left + rand - r.left;
    if (r.bottom > k.bottom - rand) dy = k.bottom - rand - r.bottom;
    if (r.top + dy < k.top + rand) dy = k.top + rand - r.top;
    if (dx || dy) {
      L.DomUtil.setPosition(el, L.DomUtil.getPosition(el).add([dx, dy]));
      el.classList.add("verschoben");
    }
  }

  // (Handler vor bindTooltip registrieren – er läuft dann vor dem Öffnen)
  function richtung(karte, marker, dx, dy) {
    marker.on("tooltipopen", (e) => einpassen(karte, e.tooltip));
    marker.on("mouseover", () => {
      const t = marker.getTooltip();
      if (!t) return;
      const oben = karte.latLngToContainerPoint(marker.getLatLng()).y < karte.getSize().y * 0.4;
      t.options.direction = oben ? "bottom" : "top";
      t.options.offset = L.point(dx, oben ? 6 : dy);
    });
  }

  function symbole(karte, ladungen) {
    const ebene = L.layerGroup().addTo(karte);
    const orte = new Map();  // gleiche Koordinaten = ein Ort (PLZ-genau)
    for (const v of ladungen) {
      const key = `${v[1]},${v[2]}`;
      if (!orte.has(key)) orte.set(key, { lat: v[1], lon: v[2], arten: new Map() });
      const o = orte.get(key), k = v[5] || "";
      if (!o.arten.has(k)) o.arten.set(k, []);
      o.arten.get(k).push(v);
    }
    let modus = farbeLesen();
    const schnitt = schnitte(ladungen);
    // Ein offener Tooltip zur Zeit; Schließen verzögert, damit man vom Schild in den Tooltip wechseln kann
    const VERZOEGERUNG = 600;
    let offen = null, gepinnt = false, timer = null;
    const schliessen = () => { clearTimeout(timer); if (offen) offen.closeTooltip(); offen = null; gepinnt = false; };
    const spaeterSchliessen = () => { clearTimeout(timer); if (!gepinnt) timer = setTimeout(schliessen, VERZOEGERUNG); };
    function haltbar(m) {
      m.off("mouseout", m.closeTooltip, m);  // Leaflets sofortiges Schließen ersetzen
      m.on("mouseover", () => {
        clearTimeout(timer);
        if (offen && offen !== m) { offen.closeTooltip(); gepinnt = false; }
        offen = m;
      });
      m.on("mouseout", spaeterSchliessen);
      m.on("click", () => { offen = m; gepinnt = true; m.openTooltip(); });
      // Leaflet positioniert einen noch offenen Tooltip beim erneuten Überfahren/Klick neu, ohne „tooltipopen“ –
      // daher auch hier einpassen (diese Handler laufen nach denen von bindTooltip)
      m.on("mouseover click", () => { if (m.isTooltipOpen()) einpassen(karte, m.getTooltip()); });
      m.on("tooltipopen", (e) => {
        const el = e.tooltip.getElement();
        L.DomEvent.disableScrollPropagation(el);  // Mausrad scrollt die Liste, nicht die Karte
        L.DomEvent.disableClickPropagation(el);
        el.onmouseenter = () => clearTimeout(timer);
        el.onmouseleave = spaeterSchliessen;
      });
    }
    karte.on("click zoomstart", schliessen);
    function breite(o) {  // geschätzte Breite der Schilder eines Orts (für die Gruppierung)
      return [...o.arten.values()].reduce((s, l) => s + Math.max(50, schildWert(l).length * 8 + 14), 0);
    }
    function zeichnen() {
      ebene.clearLayers();
      if (modus === "heat") {  // Heatmap statt Schildern: Anzahl je Ort
        schliessen();
        ebene.addLayer(heatEbene(karte.getContainer(),
          [...orte.values()].map((o) => [o.lat, o.lon, [...o.arten.values()].reduce((s, l) => s + l.length, 0)])));
        return;
      }
      const pkt = [...orte.values()].map((o) => ({ o, xy: karte.latLngToLayerPoint([o.lat, o.lon]), b: breite(o), frei: true }));
      for (const kern of pkt) {
        if (!kern.frei) continue;
        const g = pkt.filter((p) => p.frei && Math.abs(kern.xy.x - p.xy.x) < (kern.b + p.b) / 2 + 8
          && Math.abs(kern.xy.y - p.xy.y) < (kern.o.arten.size > 2 || p.o.arten.size > 2 ? 84 : 46));
        g.forEach((p) => { p.frei = false; });
        if (g.length === 1) {
          const o = kern.o, arten = ART.filter((a) => o.arten.has(a || ""));
          const lagen = arten.length === 1 ? ["mitte"] : arten.length === 2 ? ["links", "rechts"] : ["links", "rechts", "oben"];
          arten.forEach((art, i) => {
            const liste = o.arten.get(art || "");
            const m = L.marker([o.lat, o.lon], { riseOnHover: true, keyboard: false,
              icon: L.divIcon({ className: "karte-symbol", html: schild(liste, art, lagen[i], modus, schnitt), iconSize: null }) });
            const dx = { mitte: 0, links: -26, rechts: 26, oben: 0 }[lagen[i]];
            const dy = lagen[i] === "oben" ? -80 : -44;
            richtung(karte, m, dx, dy);
            m.bindTooltip(details(liste, art, modus, schnitt, true), { direction: "top", offset: [dx, dy], interactive: true,
              className: "karte-tooltip karte-tooltip-liste" });
            haltbar(m);
            m.addTo(ebene);
          });
        } else {
          const liste = g.flatMap((p) => [...p.o.arten.values()].flat());
          const mitte = [g.reduce((s, p) => s + p.o.lat, 0) / g.length, g.reduce((s, p) => s + p.o.lon, 0) / g.length];
          const m = L.marker(mitte, { keyboard: false,
            icon: L.divIcon({ className: "karte-symbol", html: gruppenSymbol(liste, modus, schnitt), iconSize: null }) });
          richtung(karte, m, 0, -52);
          m.bindTooltip(gruppenTooltip(liste, g.length, modus, schnitt), { direction: "top", offset: [0, -52], className: "karte-tooltip" })
            .on("click", () => {  // hineinzoomen, bis sich die Gruppe auflöst – mindestens 1,5 Stufen
              const grenzen = L.latLngBounds(g.map((p) => [p.o.lat, p.o.lon]));
              const ziel = Math.max(karte.getBoundsZoom(grenzen, false, L.point(160, 160)), karte.getZoom() + 1.5);
              karte.setView(grenzen.getCenter(), Math.min(ziel, karte.getMaxZoom()));
            })
            .addTo(ebene);
        }
      }
    }
    // Umschalter Farbe: AC/DC | Preis über der Karte (die Legende daneben folgt über data-farbe)
    const knoepfe = [...document.querySelectorAll("[data-kartenfarbe]")];
    const zeigen = () => {
      knoepfe.forEach((k) => k.setAttribute("aria-pressed", k.dataset.kartenfarbe === modus));
      karte.getContainer().closest(".karte-bereich")?.setAttribute("data-farbe", modus);
    };
    knoepfe.forEach((k) => k.addEventListener("click", () => {
      modus = k.dataset.kartenfarbe;
      try { localStorage.setItem(FARBE, modus); } catch (e) { /* ohne Speicher: gilt nur bis zum Neuladen */ }
      zeigen();
      zeichnen();
    }));
    zeigen();
    karte.on("zoomend", zeichnen);
    zeichnen();
  }

  // Liste (#liste) für den Kartenausschnitt nachladen; die Adresse merkt sich den Ausschnitt („bereich“)
  function listeFolgt(karte) {
    let warten = null, abbruch = null;
    karte.on("moveend", () => {
      clearTimeout(warten);
      warten = setTimeout(async () => {
        if (!karte.getContainer().isConnected) return;  // Seite inzwischen gewechselt
        const b = karte.getBounds(), r = (x) => x.toFixed(4);
        const parameter = new URLSearchParams(window.location.search);
        parameter.set("bereich", [r(b.getSouth()), r(b.getWest()), r(b.getNorth()), r(b.getEast())].join(","));
        const url = `${window.location.pathname}?${parameter}`;
        abbruch?.abort();
        abbruch = new AbortController();
        try {
          const antwort = await fetch(url, { signal: abbruch.signal, headers: { "X-Teil": "liste" } });  // nur die Liste
          const neu = new DOMParser().parseFromString(await antwort.text(), "text/html").getElementById("liste");
          const alt = document.getElementById("liste");
          if (!neu || !alt || !karte.getContainer().isConnected) return;
          alt.replaceWith(neu);
          if (window.htmx) htmx.process(neu);
          history.replaceState(history.state, "", url);
        } catch (e) { /* abgebrochen (neuer Ausschnitt) oder offline: Liste bleibt */ }
      }, 400);
    });
  }

  function zeichnen() {
    const el = document.getElementById("karte");
    if (!el || el.dataset.fertig || typeof L === "undefined" || !window.BASISKARTE) return;
    if (aktiv && !aktiv.getContainer().isConnected) { aktiv.remove(); aktiv = null; }  // Karte der vorigen Seite
    el.dataset.fertig = "1";
    const interaktiv = el.dataset.modus === "interaktiv";
    const daten = JSON.parse(document.getElementById("karte-daten").textContent);
    const karte = L.map(el, interaktiv
      ? { zoomSnap: 0.5, minZoom: 3, maxZoom: 14 }
      : { zoomSnap: 0.25, zoomControl: false, dragging: false, scrollWheelZoom: false, doubleClickZoom: false,
          boxZoom: false, keyboard: false, touchZoom: false });
    aktiv = karte;
    karte.attributionControl.setPrefix(false)
      .addAttribution("Grenzen und Orte: Natural Earth · PLZ: © GeoNames (CC-BY 4.0)");
    // Ausgangsansicht: alle Punkte (bzw. Österreich ohne Punkte)
    const punkte = interaktiv ? daten.map((v) => [v[1], v[2]]) : daten.map((p) => [p[0], p[1]]);
    const ausgangsansicht = () => (punkte.length
      ? karte.fitBounds(L.latLngBounds(punkte), { padding: [40, 40], maxZoom: interaktiv ? 12 : 9 })
      : karte.fitBounds([[46.3, 9.5], [49.1, 17.2]]));  // Österreich
    if (interaktiv) {
      homeKnopf(karte, ausgangsansicht);
      grossKnopf(karte, el);
      erstNachKlick(karte, el);
    }
    // Farblegende (Vorlage im Template) links unten in die Karte
    const vorlage = el.closest(".karte-bereich")?.querySelector(".karte-farblegende");
    if (interaktiv && vorlage) {
      const Legende = L.Control.extend({ options: { position: "bottomleft" }, onAdd() {
        const kopie = vorlage.cloneNode(true);
        kopie.hidden = false;
        L.DomEvent.disableClickPropagation(kopie);
        return kopie;
      } });
      new Legende().addTo(karte);
    }

    // Ausschnitt: gemerkter Kartenausschnitt (nach Neuladen/Zurück), sonst alle Punkte
    // Ausschnitt aus der Adresse (nach Verschieben per replaceState aktuell, auch nach „Zurück“), sonst vom Server
    const bereich = (new URLSearchParams(window.location.search).get("bereich") || el.dataset.bereich || "")
      .split(",").map(Number);
    if (interaktiv && bereich.length === 4 && bereich.every(Number.isFinite)) {
      karte.fitBounds([[bereich[0], bereich[1]], [bereich[2], bereich[3]]]);
    } else {
      ausgangsansicht();
    }
    // Basiskarte in eigenen Panes unter dem overlayPane (400) – Leaflet.heat legt sein Canvas fest in den overlayPane.
    // Von unten: Landfläche, Relief, Gewässer, Straßen, Grenzen
    const basiskarte = window.BASISKARTE;
    for (const [name, z] of [["meer", 245], ["basis", 250], ["relief", 255], ["gewaesser", 260], ["strassen", 270],
                             ["grenzen", 280]]) {
      karte.createPane(name).style.zIndex = z;
    }
    // Meer als eigene Fläche statt nur Hintergrund: das Relief wird nur mit Inhalten der Karte verrechnet
    // (multiply) – über leerem Hintergrund hellte sein Weiß das Meer auf
    L.rectangle([[-85, -540], [85, 540]], { pane: "meer", interactive: false, stroke: false,
      fillColor: farbe(el, "--karte-wasser"), fillOpacity: 1 }).addTo(karte);
    L.geoJSON(basiskarte.laender, { pane: "basis", interactive: false, style: {
      stroke: false, fillColor: farbe(el, "--karte-land"), fillOpacity: 1 } }).addTo(karte);
    L.geoJSON(basiskarte.laender, { pane: "grenzen", interactive: false, style: {
      color: farbe(el, "--karte-grenze"), weight: 1, fill: false } }).addTo(karte);
    L.geoJSON(basiskarte.regionen, { pane: "grenzen", interactive: false, style: {
      color: farbe(el, "--karte-region"), weight: 0.6, fill: false, dashArray: "3 3" } }).addTo(karte);
    kartendetails(karte, el);
    beschriftungen(karte, basiskarte);

    if (interaktiv) {
      symbole(karte, daten);
      listeFolgt(karte);  // erst nach dem ersten Ausschnitt – die Liste vom Server passt bereits dazu
    } else if (punkte.length) {
      heatEbene(el, daten.map((p) => [p[0], p[1], p[2]])).addTo(karte);
    }
  }

  // „Zurück“: htmx setzt das gespeicherte HTML ein – darin steckt die tote Karte (ohne Ereignisse, die Zoomknöpfe
  // springen dann nach oben). Alte Karte verwerfen und neu aufbauen.
  function neuAufbauen() {
    if (aktiv) { try { aktiv.remove(); } catch (e) { /* Container gehört schon zum alten DOM */ } aktiv = null; }
    const el = document.getElementById("karte");
    if (el) { el.replaceChildren(); el.className = "karte"; delete el.dataset.fertig; }  // auch „groß“ zurücksetzen
    zeichnen();
  }

  document.addEventListener("DOMContentLoaded", zeichnen);
  document.addEventListener("htmx:afterSettle", zeichnen);
  document.addEventListener("htmx:historyRestore", neuAufbauen);
  window.addEventListener("pageshow", (e) => { if (e.persisted) neuAufbauen(); });  // bfcache des Browsers
})();
