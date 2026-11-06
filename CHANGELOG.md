# Changelog

Alle nennenswerten Änderungen an ChipShop. Format lose nach
[Keep a Changelog](https://keepachangelog.com/de/1.1.0/),
Versionierung nach [SemVer](https://semver.org/lang/de/).

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

[0.0.1]: https://github.com/R-STEFFES/ChipShop/releases/tag/v0.0.1
