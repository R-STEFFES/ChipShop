# Changelog

Alle nennenswerten Änderungen an ChipShop. Format lose nach
[Keep a Changelog](https://keepachangelog.com/de/1.1.0/),
Versionierung nach [SemVer](https://semver.org/lang/de/).

## [0.1.0] – 2026-11-07

### Hinzugefügt
- **Zehn Farbschemata**: Dunkel (Standard), Schwarz·Cyan (Marke),
  Dunkel·Cyan, Mitternacht (Navy), Carbon, Matrix (Grün), Amber, Hell,
  Tageslicht und Papier – alle als CSS-Variablen, Auswahl bleibt im Browser.
  Ein Test prüft, dass jedes Theme in `app.js` auch einen CSS-Block hat.
- **README mit vier Screenshots** (Hauptansicht im Marken-Theme, GPU-Auswahl,
  Simulation, helles Theme). Dieselben Bilder liegen zusätzlich als
  Marketing-Material außerhalb des Projekts unter
  `~/Schreibtisch/STI-Marketing/chipshop/screenshots/`.
- **Footer**: statt des Autorennamens steht dort jetzt „Germany“, der Link
  auf www.steffes.industries bleibt.
- **Mehr Kerntypen**: CVA6 (6-stufig), VexiiRiscv (OoO) und Ibex
  (RV32-Mikrocontroller, Pico-2-Klasse) kommen zu Rocket und BOOM dazu. Jeder
  Kern trägt ISA-Breite und Linux-Fähigkeit; RV32/RV64-Mischungen werden
  erkannt und gemeldet. Ibex bekommt automatisch
  `WithInclusiveCacheWriteBytes(4)`.
- **NPU mit Größenwahl**: Gemmini 16×16, Gemmini 16×16 WS, Gemmini 32×32,
  NVDLA small und NVDLA large – alle als echtes Chipyard-Fragment, mit eigenem
  Floorplan-Block, MACs/TOPS, Fläche und 128-Bit-Systembus. Gemmini ist damit
  von der Modul-Checkbox in die NPU-Auswahl umgezogen (alte Projekte werden
  automatisch migriert). NVDLA baut (verifiziert, ~6 min), braucht für die
  Simulation aber eigene Workloads.
- **Linux-Ziel**: Prüfer für RV64-Kerne, L3, DRAM ≥ 256 MiB, UART,
  Block-Gerät und Boot-Flash inklusive konkreter Mängelliste und
  Klartext-Zusammenfassung.
- **Empfohlene Zusatzbausteine** für einen modernen SoC (L3 ≥ 1 MiB, 2 Banks,
  ≥ 1 GiB DRAM, Debug, Flash, Speicher-Controller, NPU, GPU) mit Knopf
  „Fehlendes ergänzen“.
- **STI-Vorlagen**: STI-Fento (1 Rocket, 130 nm, minimaler Linux-Chip),
  STI-Nano, STI-Mikro, STI-Mill und STI-Meter (8 VexiiRiscv, 7 nm,
  Gemmini 32×32, Mali-G710) sowie „Mikrocontroller“ (Ibex) und
  „16-Kern Many-Core“.
- **Echte C++-Simulation**: eigener Hintergrund-Job baut den
  Verilator-Simulator (`make -C sims/verilator`) und startet ihn mit einem
  echten RISC-V-Programm (`make run-binary-fast BINARY=…`). Live-Log,
  Exit-Code, eingesammelte Ausgabedateien und ZIP-Download.
- **Simulationspartner** (Harness-Seite, nicht Teil des ASIC): DRAM-Modell
  (SimDRAM mit einstellbarer Extra-Latenz oder SimAXIMem mit einem Zyklus),
  DRAM-Größe, MMIO-Speicher, UART-Adapter, SerialTL/TSI, SimJTAG, SimDMI,
  SPI-Flash, Block-Device und NIC-Loopback. Basis-Partner sind als solche
  gekennzeichnet, ergänzbare werden als `chipyard.harness.*`-Fragment
  geschrieben.
- **L3-Cache konfigurierbar**: an/aus, Größe (128 KiB–4 MiB), Assoziativität
  und Banks. Aus erzeugt `WithBroadcastManager` statt `WithInclusiveCache`.
- **GPU-Auswahlfenster** mit sechs GPUs (Mali-G78/G710, Intel Xe-LP,
  PowerVR, Vortex, eigene RoCC-Engine), Fläche für den gewählten Prozess
  gerechnet; die GPU erscheint als eigener Block im Floorplan und in
  Fläche/Leistung/Rechenleistung.
- **Gesperrtes Fensterlayout**: die Seite scrollt nicht mehr, jede Spalte
  scrollt in sich (`col-head` bleibt stehen, `col-body` hat den eigenen
  Balken); Kopf und Fuß bleiben immer sichtbar.
- **Idiotensichere Oberfläche**: nummerierte Schritte, Erklärtexte je Feld
  („Was heißt das?“), Klartext-Zusammenfassung des Chips, Hilfe-Dialog,
  Status-Pillen für Checkout/Conda/Simulator.
- Gemischte Rocket-/BOOM-Konfigurationen mit bis zu 64 Kernen; Schätzung,
  Floorplan und Scala-Config berücksichtigen jeden Kerntyp einzeln.
- JSON-Projekte speichern/laden (Format v2 mit L3-, GPU- und
  Simulationsangaben; alte Dateien mit `l2_kib` werden weiter geladen),
  einstellbares Farbschema und Modul-Tooltips.
- Größere Floorplan-Vorschau; Schätzung und Simulationsaufwandskarten stehen
  jetzt in der rechten Spalte oberhalb des Verilog-Bereichs.
- RAM-Warnung vor Builds sowie automatische Diagnosehinweise aus Build-Logs.
- Shelf-Packing sucht mehrere Breiten ab und minimiert Die-Fläche inklusive
  IO-Ring statt eine feste Breite anzunehmen.
- Build-Diagnose greift jetzt auch bei Compile- und Simulationsfehlern
  (Zeitlimit wird als solches erkannt).
- Die Config wird nur neu geschrieben, wenn sich der Scala-Code ändert –
  reine Kommentaränderungen lösen keinen RTL-/Simulator-Rebuild mehr aus.
- Tests: 45 Fälle, darunter L3-Varianten, GPU/Floorplan, DRAM-Parameter,
  Simulationspartner samt Fragment-Existenzprüfung und die neuen Endpunkte.

### Geändert
- „L2/LLC“ heißt jetzt durchgehend **L3/LLC** (Chipyards `InclusiveCache`
  ist die letzte Cache-Ebene oberhalb der Kern-L1).
- Die API nimmt weiterhin `l2_kib` als Alt-Feld an.


## [0.0.1] – 2026-11-07

Erster veröffentlichter Stand.

### Enthalten
- Flask-Oberfläche in drei Spalten: konfigurieren, schätzen, bauen.
- Kern-/Modul-/PDK-Auswahl mit Schnellvorlagen, L2-/LLC-Größe,
  RoCC-Demo-Beschleuniger, Aktivität und Simulationslänge.
- Schätzung von Die-Fläche, Takt, Leistung, Rechenleistung, Wafer-Ausbeute,
  Verilator-/FireSim-Dauer und Host-Bedarf inklusive ausgewiesener Annahmen.
- Vorab-Floorplan als SVG (Shelf-Packing, Kern-Cluster, IO-Ring).
- Scala-Config rendern, anzeigen und ins Chipyard-Checkout schreiben.
- `make verilog` als Hintergrund-Job mit Live-Status, Log-Ausschnitt,
  Dateiliste und ZIP-Download; frühere Läufe bleiben abrufbar.
- 14 Unit-Tests für Schätzmodell und Config-Generierung
  (`FragmentExistenceTest` prüft jedes Fragment gegen das Checkout).
- Logo `assets/logo.png` (1024×1024) und README-Banner `assets/hero.png`
  (1024×512) im Steffes-Industries-Stil: schwarz, weiße kursive Wortmarke mit
  Überstrich und Speed-Slash, Cyan #00E5FF.
- Markenauftritt durchgezogen: ChipShop-Lockup im Header der Oberfläche,
  kleines Steffes-Industries-Logo mit Link auf www.steffes.industries im
  Footer, Favicon aus dem ChipShop-Die-Symbol, Autorenzeile im Banner, eigener
  README-Abschnitt mit Screenshot.
- Steffes-Industries-Logo korrigiert: statt der zuvor verwendeten Marke jetzt
  das offizielle Logo `google_STI_logo.png` (PR/Logo) – „STEFFES" aufrecht mit
  Unterstrich, „INDUSTRIES" kursiv mit Überstrich. Die weiße Fassung wird aus
  dem Alphakanal desselben Logos erzeugt (die Dateien `..._Schmal/_Weiß` im
  Bestand sind konturierte Varianten und ergeben kein gefülltes Weiß).
  Banner, README und Footer entsprechend neu.
- `[hidden] { display: none !important; }` in `style.css`: das Build-Panel ist
  jetzt wirklich versteckt, solange kein Build läuft (`display: flex` hatte das
  `hidden`-Attribut zuvor überschrieben).

[0.1.0]: https://github.com/R-STEFFES/ChipShop/releases/tag/v0.1.0
[0.0.1]: https://github.com/R-STEFFES/ChipShop/releases/tag/v0.0.1
