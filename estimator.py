"""Chipyard GUI -- Schaetz-Engine.

Reine, seiteneffektfreie Funktionen: keine Datei-/Netzwerkzugriffe, kein Flask.
Dadurch ist die komplette Mathematik isoliert testbar (tests/test_estimator.py).

Alle Zahlen sind bewusst GROBE Richtwerte ("order of magnitude"), gedacht fuer
eine erste Einordnung VOR Synthese/Place&Route/Simulation. Jede Annahme steht
direkt am Eintrag und wird im UI mit angezeigt (Feld "assumptions").
"""

from __future__ import annotations

import math

# --------------------------------------------------------------------------
# Globale Annahmen
# --------------------------------------------------------------------------

AREA_REF_NM = 28.0        # Referenzknoten fuer alle Flaschenwerte unten
AREA_SCALE_EXP = 1.30     # Flasche skaliert mit (nm/ref)^exp (1.3 statt 2.0,
                          # weil SRAM & Verdrahtung langsamer schrumpfen)
ROUTING_OVERHEAD = 1.30   # Verdrahtung, Takthueme, Whitespace auf Block-Flaechen
IO_RING_MM = 0.20         # Breite des Pad-/IO-Rings um das Die
MIN_DIE_SIDE_MM = 0.60    # kleinste sinnvolle Die-Kante (Pad-Limited)
WAFER_DIAMETER_MM = 300.0 # Standard-Wafer
SCRIBE_MM = 0.08          # Saegegassen pro Die


# --------------------------------------------------------------------------
# Fertigungs-Prefabs (Prozess-Knoten)
# --------------------------------------------------------------------------

PRESETS = {
    "sky130": dict(
        label="SkyWater SKY130 (Open PDK)", node_nm=130, vdd=1.8,
        fmax_ghz=0.10, power_w_per_mm2=0.050, leak_frac=0.10,
        defect_per_cm2=0.50, kind="Open Source",
        note="frei verfuegbarer 130-nm-CMOS, ueber Efabless/Google tapeout-bar",
    ),
    "gf180": dict(
        label="GlobalFoundries GF180MCU (Open PDK)", node_nm=180, vdd=3.3,
        fmax_ghz=0.06, power_w_per_mm2=0.040, leak_frac=0.06,
        defect_per_cm2=0.55, kind="Open Source",
        note="Open-Source-180-nm, 3.3-V-IO, gut fuer Mixed-Signal",
    ),
    "ihp130": dict(
        label="IHP SG13G2 (Open PDK, BiCMOS)", node_nm=130, vdd=1.2,
        fmax_ghz=0.12, power_w_per_mm2=0.060, leak_frac=0.08,
        defect_per_cm2=0.45, kind="Open Source",
        note="130-nm-SiGe-BiCMOS, sehr schnelle HBTs, 1.2-V-Core",
    ),
    "nangate45": dict(
        label="Nangate OpenCell 45 nm", node_nm=45, vdd=1.1,
        fmax_ghz=0.40, power_w_per_mm2=0.160, leak_frac=0.20,
        defect_per_cm2=0.30, kind="Akademisch",
        note="Standard-Zellen-Bibliothek fuer Lehre/Forschung, kein Tapeout",
    ),
    "asap7": dict(
        label="ASAP7 7 nm (praediktiv)", node_nm=7, vdd=0.70,
        fmax_ghz=1.10, power_w_per_mm2=0.300, leak_frac=0.35,
        defect_per_cm2=0.10, kind="Praediktiv",
        note="vorhersagende 7-nm-Bibliothek (ASU), keine echten Masken",
    ),
    "tsmc28": dict(
        label="TSMC 28 nm (kommerziell, Referenz)", node_nm=28, vdd=0.90,
        fmax_ghz=0.80, power_w_per_mm2=0.220, leak_frac=0.15,
        defect_per_cm2=0.12, kind="Kommerziell",
        note="Referenzknoten; reale Foundry-Zahlen sind NDA-geschuetzt",
    ),
    "generic7": dict(
        label="Generisch 7 nm HPC", node_nm=7, vdd=0.75,
        fmax_ghz=1.80, power_w_per_mm2=0.550, leak_frac=0.40,
        defect_per_cm2=0.09, kind="Praediktiv",
        note="optimaler High-Performance-7-nm-Knoten (Obergrenze)",
    ),
}


# --------------------------------------------------------------------------
# Kern-Typen
# --------------------------------------------------------------------------

CORE_TYPES = {
    "rocket": dict(
        label="Rocket -- in-order, RV64GC", area28=0.22,
        dmips_per_mhz=1.7, fmax_ghz=1.40, flops_per_cycle=2.0, l1_kib=32,
        uarch="in-order, 1-wide, FPU (Zfh/Zfinx)", bits=64, linux=True,
        fragment="freechips.rocketchip.rocket.WithNHugeCores",
        note="Der Standardkern: klein, robust, läuft Linux ab 1 Kern.",
    ),
    "boom_small": dict(
        label="BOOM - Small -- out-of-order", area28=0.85,
        dmips_per_mhz=2.6, fmax_ghz=1.20, flops_per_cycle=2.0, l1_kib=32,
        uarch="OoO, 2-wide decode, 1 Branch-Unit", bits=64, linux=True,
        fragment="boom.v4.common.WithNSmallBooms",
        note="Out-of-order, mehr Leistung pro Takt als Rocket.",
    ),
    "boom_medium": dict(
        label="BOOM - Medium -- out-of-order", area28=1.65,
        dmips_per_mhz=3.4, fmax_ghz=1.10, flops_per_cycle=2.0, l1_kib=48,
        uarch="OoO, 3-wide decode, 2 Branch-Units", bits=64, linux=True,
        fragment="boom.v4.common.WithNMediumBooms",
        note="Mittelklasse-OoO für Linux und anspruchsvolle Software.",
    ),
    "boom_large": dict(
        label="BOOM - Large -- out-of-order", area28=3.20,
        dmips_per_mhz=4.2, fmax_ghz=1.00, flops_per_cycle=2.0, l1_kib=64,
        uarch="OoO, 4-wide decode, 3 Branch-Units", bits=64, linux=True,
        fragment="boom.v4.common.WithNLargeBooms",
        note="Größter BOOM: viel Fläche, viel Leistung.",
    ),
    "cva6": dict(
        label="CVA6 -- 6-stufig, RV64GC", area28=1.10,
        dmips_per_mhz=2.1, fmax_ghz=1.20, flops_per_cycle=2.0, l1_kib=32,
        uarch="in-order, 6-stufig, Linux-fähig (MMU)", bits=64, linux=True,
        fragment="cva6.WithNCVA6Cores",
        note="Linux-fähiger Anwendungskern (ehemals Ariane).",
    ),
    "vexii": dict(
        label="VexiiRiscv -- OoO, RV64GC", area28=2.30,
        dmips_per_mhz=3.8, fmax_ghz=1.60, flops_per_cycle=2.0, l1_kib=32,
        uarch="skalierbarer OoO-Kern, Linux-fähig", bits=64, linux=True,
        fragment="vexiiriscv.WithNVexiiRiscvCores",
        note="Moderner OoO-Kern für hohe Taktraten und Linux.",
    ),
    "ibex": dict(
        label="Ibex -- Mikrocontroller, RV32 (Pico-Klasse)", area28=0.06,
        dmips_per_mhz=0.9, fmax_ghz=0.50, flops_per_cycle=0.0, l1_kib=16,
        uarch="2-stufig, RV32IMC, kein MMU", bits=32, linux=False,
        fragment="ibex.WithNIbexCores",
        extra_fragments=["chipyard.config.WithInclusiveCacheWriteBytes(4)"],
        note="Kleinstkern wie im Raspberry Pi Pico 2 (dort Hazard3): "
             "Mikrocontroller, kein Linux.",
    ),
}

# --------------------------------------------------------------------------
# NPU-Katalog (Beschleuniger für KI/Matrix)
# --------------------------------------------------------------------------
# Gemmini und NVDLA liegen in diesem Checkout als echte Generatoren; die
# Größe bestimmt das systolische Array bzw. die NVDLA-Konfiguration.
NPUS = [
    dict(
        id="gemmini_16", label="Gemmini 16x16 (klein)", family="Gemmini",
        macs_per_cycle=256, area28=1.60,
        fragment="gemmini.DefaultGemminiConfig",
        note="Systolisches 16x16-Array, 64 KiB Scratchpad + 32 KiB Akkumulator",
    ),
    dict(
        id="gemmini_chip", label="Gemmini Chip-Config (16x16, WS)",
        family="Gemmini", macs_per_cycle=256, area28=1.85,
        fragment="gemmini.DefaultGemminiConfig(gemmini.GemminiConfigs.chipConfig)",
        note="16x16 mit Weight-Stationary-Dataflow, kleinere Puffer",
    ),
    dict(
        id="gemmini_32", label="Gemmini 32x32 (groß)", family="Gemmini",
        macs_per_cycle=1024, area28=5.20,
        fragment="gemmini.DefaultGemminiConfig(gemmini.GemminiConfigs.largeChipConfig)",
        note="Systolisches 32x32-Array, 128 KiB Scratchpad + 64 KiB Akkumulator",
    ),
    dict(
        id="nvdla_small", label="NVDLA small", family="NVDLA",
        macs_per_cycle=512, area28=10.0,
        fragment='nvidia.blocks.dla.WithNVDLA("small")',
        note="NVIDIA-CNN-Beschleuniger, kleine Konfiguration; baut, braucht "
             "aber eigene NVDLA-Workloads",
    ),
    dict(
        id="nvdla_large", label="NVDLA large (synthetische RAMs)",
        family="NVDLA", macs_per_cycle=1024, area28=15.0,
        fragment='nvidia.blocks.dla.WithNVDLA("large", true)',
        note="Große NVDLA-Konfiguration; Build dauert deutlich länger",
    ),
]
NPUS_BY_ID = {n["id"]: n for n in NPUS}
NPU_SIZE_CHOICES = [n["id"] for n in NPUS]



# --------------------------------------------------------------------------
# Modul-Katalog
# --------------------------------------------------------------------------
# "fragment" = echter Chipyard-Scala-Baustein (wird in den Config-Code
#              geschrieben -> generierbar).
# "available: False" = in diesem Chipyard-Checkout KEIN fertiges Fragment
#              vorhanden -> nur fuer Layout-/Leistungsschaetzung.

MODULES = [
    # ---- Peripherie -------------------------------------------------------
    dict(id="uart", label="UART", category="Peripherie", area28=0.02,
         fragment=None, in_base=True,
         remove_fragment="chipyard.config.WithNoUART", default=True,
         note="Serielle Konsole (im Sim an stdout gebunden); steckt bereits "
              "im Basis-Config (AbstractConfig)"),
    dict(id="gpio", label="GPIO", category="Peripherie", area28=0.05,
         fragment="chipyard.config.WithGPIO", default=True,
         note="Parallel-Port zur Aussenwelt"),
    dict(id="spi", label="SPI", category="Peripherie", area28=0.04,
         fragment="chipyard.config.WithSPI", default=False,
         note="SPI-Master"),
    dict(id="i2c", label="I2C", category="Peripherie", area28=0.03,
         fragment="chipyard.config.WithI2C", default=False,
         note="I2C-Bus"),
    dict(id="spiflash", label="SPI-Flash", category="Peripherie", area28=0.06,
         fragment="chipyard.config.WithSPIFlash", default=False,
         note="Boot-Flash-Anbindung"),
    dict(id="dmi", label="Debug (DMI/JTAG)", category="Peripherie", area28=0.02,
         fragment="chipyard.config.WithDMIDTM", default=True,
         note="Debug-Transport-Modul, JTAG -> DMI"),
    dict(id="blockdev", label="Block-Geraet (TL)", category="Peripherie", area28=0.15,
         fragment="testchipip.iceblk.WithBlockDevice", default=False,
         note="Speicher-Block-Device am Periphery-Bus"),

    # ---- Beschleuniger ---------------------------------------------------
    # Gemmini/NVDLA sind keine Checkbox-Module mehr, sondern die NPU-Auswahl
    # (siehe NPUS) mit Groessenwahl.

    # ---- nur Schaetzung (kein Fragment in diesem Checkout) ---------------
    dict(id="ara", label="Ara -- Vektor-Unit",
         category="Beschleuniger (nur Schaetzung)", area28=0.50,
         fragment=None, available=False, macs_per_cycle=64, default=False,
         note="RISC-V-Vektorunit, 2 Lanes x 4096 bit"),
    dict(id="hwacha", label="Hwacha -- Vektor-Unit",
         category="Beschleuniger (nur Schaetzung)", area28=0.60,
         fragment=None, available=False, macs_per_cycle=64, default=False,
         note="Aeltere Vektorunit, 4 Lanes x 64 bit"),
    dict(id="icenet", label="IceNIC -- Netzwerk-Offload",
         category="Beschleuniger (nur Schaetzung)", area28=0.45,
         fragment=None, available=False, macs_per_cycle=0, default=False,
         note="Netzwerk-Interface-Controller fuer FireSim"),
]

MODULES_BY_ID = {m["id"]: m for m in MODULES}

# Immer enthaltene Infrastruktur (nicht abwaehlbar)
UNCORE_AREA28 = 0.60      # sbus/mbus/pbus/cbus/fbus/obus, PLIC, CLINT,
                          # BootROM, 64 KiB Scratchpad, Debug-Logik

# --------------------------------------------------------------------------
# L3 / LLC
# --------------------------------------------------------------------------
# In Chipyard ist die gemeinsame, inklusive Cache-Ebene (InclusiveCache) die
# letzte Cache-Stufe vor dem DRAM; sie sitzt oberhalb der L1 der Kerne.
# Deshalb fuehrt die Oberflaeche sie als L3/LLC.
L3_AREA28_PER_KIB = 0.00215   # SRAM + Tags pro KiB (512 KiB ~ 1.10 mm2 @28 nm)
L3_DIRECTORY_AREA28_PER_BANK = 0.045   # Verzeichnis/Probe-Logik je Bank
L3_SIZE_CHOICES = [128, 256, 512, 1024, 2048, 4096]
L3_WAYS_CHOICES = [2, 4, 8, 16]
L3_BANK_CHOICES = [1, 2, 4, 8]
NO_L3_HUB_AREA28 = 0.09       # Broadcast-Hub, wenn die L3 abgeschaltet ist

# Rueckwaerts-Kompatibilitaet: alte Projekte/API-Aufrufe nennen es "L2".
L2_AREA28_PER_KIB = L3_AREA28_PER_KIB
L2_SIZE_CHOICES = L3_SIZE_CHOICES


# --------------------------------------------------------------------------
# GPU-Katalog
# --------------------------------------------------------------------------
# "fragment" bleibt bei allen Eintraegen leer: in diesem Chipyard-Checkout
# gibt es keinen GPU-Generator. Die GPU fliesst damit in Flaeche/Leistung/
# Floorplan ein, erscheint aber nicht im generierten Verilog -- die Oberflaeche
# sagt das an der Auswahl und bei der Config-Erzeugung deutlich an.
GPUS = [
    dict(
        id="mali_g78", label="ARM Mali-G78 MP8", vendor="ARM",
        kind="integriert", area28=58.0, fmax_ghz=0.75, flops_per_cycle=2048,
        vram_gib=0.0, shared_memory=True, api="Vulkan, OpenGL ES 3.2",
        note="8 Shader-Kerne, tile-basiert, typische Smartphone-iGPU",
    ),
    dict(
        id="mali_g710", label="ARM Mali-G710 MP10", vendor="ARM",
        kind="integriert", area28=74.0, fmax_ghz=0.90, flops_per_cycle=4096,
        vram_gib=0.0, shared_memory=True, api="Vulkan 1.2, OpenCL 2.0",
        note="10 Shader-Kerne, Oberklasse-Smartphone/Tablet",
    ),
    dict(
        id="intel_uhd", label="Intel UHD (Xe-LP, 32 EU)", vendor="Intel",
        kind="integriert", area28=52.0, fmax_ghz=1.10, flops_per_cycle=2048,
        vram_gib=0.0, shared_memory=True, api="Vulkan 1.2, OpenGL 4.6",
        note="32 Execution Units, Notebook-Klasse, teilt sich den DRAM",
    ),
    dict(
        id="powervr_gt", label="Imagination PowerVR GT7600 Plus",
        vendor="Imagination", kind="integriert", area28=38.0, fmax_ghz=0.75,
        flops_per_cycle=1024, vram_gib=0.0, shared_memory=True,
        api="Vulkan 1.1, OpenGL ES 3.2",
        note="6 Cluster, tile-basiert (TBDR), sehr flaecheneffizient",
    ),
    dict(
        id="vortex", label="Vortex RISC-V GPGPU", vendor="Open Source",
        kind="diskret (offen)", area28=64.0, fmax_ghz=1.00,
        flops_per_cycle=4096, vram_gib=4.0, shared_memory=False,
        api="OpenCL 1.2",
        note="offener GPGPU-Generator (nicht in diesem Checkout installiert)",
    ),
    dict(
        id="custom_rocc", label="Eigene Grafik-/Matrix-Engine (RoCC)",
        vendor="Eigenbau", kind="integriert", area28=30.0, fmax_ghz=1.00,
        flops_per_cycle=1024, vram_gib=0.0, shared_memory=True,
        api="eigene Treiber",
        note="Platzhalter fuer eine selbst gebaute Beschleuniger-Einheit",
    ),
]
GPUS_BY_ID = {g["id"]: g for g in GPUS}

# --------------------------------------------------------------------------
# Simulationspartner (Harness-Seite, NICHT Teil des ASIC)
# --------------------------------------------------------------------------
# Diese Module sitzen im TestHarness der C++-Simulation: DRAM-Modell, UART-
# Adapter, Block-Device usw. Sie werden als chipyard.harness.*-Fragmente in
# dieselbe Config geschrieben (sie ueberschreiben bei Bedarf die Basis-Binder
# aus AbstractConfig, weil links stehende Fragmente Vorrang haben).
DRAM_MODELS = [
    dict(id="simdram", label="SimDRAM (Timing-Modell)",
         fragment="chipyard.harness.WithBlackBoxSimMem",
         note="reales DRAM-Timing, langsam -- naeher an der echten Hardware"),
    dict(id="aximem", label="SimAXIMem (1-Zyklus-Speicher)",
         fragment="chipyard.harness.WithSimAXIMem",
         note="magischer Speicher ohne Latenz, deutlich schneller"),
]

SIM_PARTNERS = [
    dict(id="dram", label="DRAM-Modell", category="Speicher", in_base=True,
         default=True,
         note="Hauptspeicher der Simulation; Groesse und Modell sind waehlbar"),
    dict(id="mmio", label="MMIO-Speicher (SimAXIMem)", category="Speicher",
         in_base=True, default=True,
         fragment="chipyard.harness.WithSimAXIMMIO",
         note="Antwortet auf MMIO-Zugriffe ausserhalb des DRAM"),
    dict(id="uart", label="UART auf stdout", category="Konsolen",
         in_base=True, default=True,
         fragment="chipyard.harness.WithUARTAdapter",
         note="Serielle Ausgabe des Programms erscheint im Simulations-Log"),
    dict(id="serialtl", label="SerialTL/TSI (Host-Anbindung)",
         category="Konsolen", in_base=True, default=True,
         fragment="chipyard.harness.WithSimTSIOverSerialTL",
         note="Laedt das Programm und verbindet den Host mit dem Chip"),
    dict(id="jtag", label="SimJTAG (Debug)", category="Debug", in_base=True,
         default=True, fragment="chipyard.harness.WithSimJTAGDebug",
         note="Debug-Zugriff ueber JTAG, wie am echten Board"),
    dict(id="dmi", label="SimDMI (Debug-Transport)", category="Debug",
         in_base=True, default=True,
         fragment="chipyard.harness.WithSimDMI",
         note="Debug-Modul-Anbindung ueber DMI"),
    dict(id="spiflash", label="SPI-Flash-Modell", category="Peripherie",
         in_base=True, default=True,
         fragment="chipyard.harness.WithSimSPIFlashModel",
         note="Simuliertes Boot-Flash; abschaltbar auf Nur-Lesen"),
    dict(id="blkdev", label="Block-Device-Modell", category="Peripherie",
         in_base=False, default=False,
         fragment="chipyard.harness.WithSimBlockDevice",
         requires=["blockdev"],
         note="Festplatten-Emulation; braucht das SoC-Modul Block-Geraet"),
    dict(id="nic", label="NIC-Loopback", category="Peripherie",
         in_base=False, default=False,
         fragment="chipyard.harness.WithLoopbackNIC",
         note="Netzwerk-Pakete werden zurueckgeschickt; braucht ein NIC-Modul"),
]

SIM_PARTNERS_BY_ID = {p["id"]: p for p in SIM_PARTNERS}

# DRAM-Groessen in MiB (Adressraum des SoC, nicht der Modelle)
DRAM_SIZE_CHOICES = [64, 128, 256, 512, 1024, 2048, 4096, 8192]

# RoCC-Demo-Beschleuniger (chipyard.config.WithAccumulatorRoCC)
ROCC_AREA28_EACH = 0.02


# --------------------------------------------------------------------------
# Grundrechenarten
# --------------------------------------------------------------------------

def node_scale(node_nm: float, ref_nm: float = AREA_REF_NM,
               exp: float = AREA_SCALE_EXP) -> float:
    """Flaechen-Skalierungsfaktor von ref_nm auf node_nm (>1 = groesser).

    Groesserer Struktur-Knoten (mehr nm) -> groessere Flaeche. Beispiel:
    28 nm -> 130 nm ergibt (130/28)^1.3 = 7.4.
    """
    return (float(node_nm) / ref_nm) ** exp


def scaled_area(area28: float, node_nm: float) -> float:
    """Flasche @28 nm -> Flasche @node_nm (mm2)."""
    return area28 * node_scale(node_nm)


# --------------------------------------------------------------------------
# Floorplan: Shelf-Packing
# --------------------------------------------------------------------------

def _shelf_pack(blocks, target_w=None):
    """Einfaches Shelf-/Regal-Packing. blocks: dicts mit 'area' und 'aspect'.

    Liefert (placed, width, height). Jeder platzierte Block bekommt x/y/w/h.
    """
    items = []
    for b in blocks:
        area = max(float(b.get("area", 0.0)), 1e-9)
        aspect = float(b.get("aspect", 1.0)) or 1.0
        w = math.sqrt(area * aspect)
        h = area / w
        items.append({**b, "w": w, "h": h})

    items.sort(key=lambda i: -i["h"])

    def pack(width_limit):
        placed = [dict(item) for item in items]
        x = y = row_h = 0.0
        width = 0.0
        for it in placed:
            if x > 0.0 and x + it["w"] > width_limit:
                x = 0.0
                y += row_h
                row_h = 0.0
            it["x"], it["y"] = x, y
            x += it["w"]
            row_h = max(row_h, it["h"])
            width = max(width, x)
        height = y + row_h
        return placed, width, height

    if target_w is not None:
        return pack(target_w)

    if not items:
        return [], 0.0, 0.0

    # A single sqrt(total-area) guess can leave large holes when blocks have
    # very different aspect ratios. Evaluate a bounded set of widths and keep
    # the placement with the smallest die including its IO-ring margin.
    min_width = max(item["w"] for item in items)
    max_width = sum(item["w"] for item in items)
    candidates = {
        min_width + (max_width - min_width) * i / 64.0
        for i in range(65)
    }
    total = sum(item["area"] for item in items)
    candidates.update(math.sqrt(total * ratio)
                      for ratio in (0.8, 0.9, 1.0, 1.1, 1.2, 1.3, 1.4, 1.5))
    candidates = {max(min_width, min(width, max_width)) for width in candidates}

    best = None
    for width_limit in candidates:
        result = pack(width_limit)
        score = (result[1] + 2 * IO_RING_MM) * (result[2] + 2 * IO_RING_MM)
        if best is None or score < best[0]:
            best = (score, result)
    return best[1]


def build_floorplan(cores: int, core_area_mm2: float, l2_area_mm2: float,
                    module_blocks, uncore_area: float, core_mix=None,
                    core_tile_areas=None, l3_label: str = "L3 / LLC"):
    """Baut den groben Floorplan mit einem eigenen Cluster je Kerntyp."""
    blocks = []

    # --- Kerntyp-Cluster als getrennte Grids ------------------------------
    mix = core_mix or {"rocket": cores}
    tile_areas = core_tile_areas or {}
    for core_type, count in mix.items():
        if count <= 0:
            continue
        cols = int(math.ceil(math.sqrt(count)))
        rows = int(math.ceil(count / float(cols)))
        tile_area = tile_areas.get(core_type, core_area_mm2)
        cw = ch = math.sqrt(tile_area * 1.12)
        cluster_w, cluster_h = cols * cw, rows * ch
        label = CORE_TYPES[core_type]["label"].split(" -- ")[0]
        blocks.append(dict(
            key="cores_" + core_type,
            label="%d x %s" % (count, label), kind="cores",
            core_type=core_type, area=cluster_w * cluster_h,
            aspect=cluster_w / cluster_h if cluster_h else 1.0,
            cores=count, cols=cols, rows=rows, tile=tile_area,
        ))

    # --- L2 / LLC ---------------------------------------------------------
    if l2_area_mm2 > 0:
        blocks.append(dict(key="l2", label=l3_label, kind="memory",
                           area=l2_area_mm2, aspect=4.0))

    # --- Uncore (Busse, PLIC, BootROM, ...) -------------------------------
    blocks.append(dict(key="uncore", label="Uncore / Interconnect",
                       kind="uncore", area=uncore_area, aspect=3.0))

    # --- Modul-Bloecke ----------------------------------------------------
    blocks.extend(module_blocks)

    placed, w, h = _shelf_pack(blocks)

    die_w = max(w + 2 * IO_RING_MM, MIN_DIE_SIDE_MM)
    die_h = max(h + 2 * IO_RING_MM, MIN_DIE_SIDE_MM)

    return dict(
        blocks=placed,
        content_w=w, content_h=h,
        die_w=die_w, die_h=die_h,
        io_ring=IO_RING_MM,
        die_area_content=w * h,
    )


# --------------------------------------------------------------------------
# Wafer
# --------------------------------------------------------------------------

def dies_per_wafer(die_side_mm: float, diameter_mm: float = WAFER_DIAMETER_MM,
                   scribe_mm: float = SCRIBE_MM) -> float:
    """Brutto-Dies/Wafer nach der Standard-Naeherung.

    DPW = pi*d^2/(4*A) - pi*d/sqrt(2*A)   mit A = (side+scribe)^2 in mm2.
    """
    side = die_side_mm + scribe_mm
    a = side * side
    d = diameter_mm
    dpw = math.pi * d * d / (4.0 * a) - math.pi * d / math.sqrt(2.0 * a)
    return max(0.0, dpw)


def murphy_yield(die_area_mm2: float, defect_per_cm2: float) -> float:
    """Murphy-Yield-Modell: Y = ((1 - e^-x)/x)^2, x = A[cm2] * D[1/cm2]."""
    a_cm2 = die_area_mm2 / 100.0
    x = a_cm2 * defect_per_cm2
    if x <= 1e-9:
        return 1.0
    return ((1.0 - math.exp(-x)) / x) ** 2


# --------------------------------------------------------------------------
# Simulations-Aufwand (Verilator / FireSim)
# --------------------------------------------------------------------------

def sim_estimate(cores: int, core_type: str, accel_mac_total: int,
                 cycles_millions: float, core_mix=None, dram_mib: int = 256,
                 dram_model: str = "simdram", sim_partners=None,
                 gpu_gflops: float = 0.0):
    """Grober Abschaetzung des Host-Aufwands fuer eine Simulation."""
    mix = core_mix or {core_type: cores}
    average_core_cost = sum(
        count * CORE_TYPES[kind]["area28"] / CORE_TYPES["rocket"]["area28"]
        for kind, count in mix.items()
    ) / max(cores, 1)

    # Verilator-Durchsatz: 1 Rocket-Kern ~ 2.6 Mcycles/s auf einem modernen
    # Host-Kern; weitere und schwerere Kerne kosten entsprechend mehr.
    base_throughput = 2.6
    throughput = base_throughput / (1.0 + 0.55 * max(0, cores - 1)) / average_core_cost
    # Das DRAM-Modell bremst: SimDRAM braucht fuer jede Anfrage mehrere
    # Zyklen, SimAXIMem antwortet in einem.
    dram_factor = 0.62 if dram_model == "simdram" else 1.35
    throughput *= dram_factor
    throughput = max(throughput, 0.02)

    wall_h = cycles_millions / throughput / 3600.0

    partners = list(sim_partners or [])
    # SimDRAM/SimAXIMem allozieren den kompletten DRAM-Bereich im Host-RAM.
    dram_gib = max(0.0, dram_mib / 1024.0) * 1.2
    ram_gb = 2.5 + 0.8 * cores + dram_gib + (0.5 if accel_mac_total > 0 else 0.0)
    ram_gb += 0.4 * len([p for p in partners if p not in ("dram", "mmio")])
    build_min = 6.0 + 1.8 * cores + (4.0 if accel_mac_total > 0 else 0.0)
    # C++-Simulator uebersetzen (Verilator) kostet je Kern einige Minuten
    build_min += 3.0 + 1.2 * cores

    return dict(
        cycles_millions=cycles_millions,
        verilator_throughput_mcyc_s=throughput,
        verilator_wall_hours=wall_h,
        host_ram_gb=ram_gb,
        build_minutes_estimate=build_min,
        firesim_sim_mhz=100.0,
        firesim_wall_hours=cycles_millions / (100.0 * 3600.0 / 1000.0),
        dram_mib=dram_mib,
        dram_model=dram_model,
        sim_partners=partners,
        gpu_gflops=gpu_gflops,
        note=("Verilator ist software-emuliert und langsam aber guenstig; "
              "FireSim nutzt eine FPGA-Karte und laeuft ~1000x schneller, "
              "braucht aber Cloud-/FPGA-Hardware."),
    )


# --------------------------------------------------------------------------
# Hauptfunktion
# --------------------------------------------------------------------------

def _core_count(value, core_type: str) -> int:
    if isinstance(value, bool):
        raise ValueError("Ungültige Kernanzahl für %s." % core_type)
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ValueError("Ungültige Kernanzahl für %s." % core_type)
    if not math.isfinite(number) or not number.is_integer() or number < 0:
        raise ValueError("Ungültige Kernanzahl für %s." % core_type)
    return int(number)


def normalize_core_mix(spec: dict):
    """Validiert die gemischte Kernauswahl und liefert Mix, Summe, Primärtyp."""
    core_type = spec.get("core_type", "rocket")
    if core_type not in CORE_TYPES:
        raise ValueError("Unbekannter Kern-Typ: %r" % (core_type,))

    raw_mix = spec.get("core_mix")
    if raw_mix is None:
        core_mix = {core_type: _core_count(spec.get("cores", 1), core_type)}
    else:
        if not isinstance(raw_mix, dict):
            raise ValueError("core_mix muss eine Zuordnung aus Kerntyp und Anzahl sein.")
        core_mix = {}
        for kind, value in raw_mix.items():
            if kind not in CORE_TYPES:
                raise ValueError("Unbekannter Kern-Typ: %r" % (kind,))
            count = _core_count(value, kind)
            if count:
                core_mix[kind] = count

    cores = sum(core_mix.values())
    if cores < 1:
        raise ValueError("Mindestens 1 Kern noetig.")
    if cores > 64:
        raise ValueError("Maximal 64 Kerne (darueber ist die Naeherung unrelevant).")
    if core_type not in core_mix:
        core_type = max(core_mix, key=core_mix.get)
    return core_mix, cores, core_type


def linux_readiness(core_mix, modules, l3_enabled, dram_mib) -> dict:
    """Prueft, ob die Konfiguration Linux booten koennte."""
    issues = []
    bits = {CORE_TYPES[k]["bits"] for k in core_mix}
    if 32 in bits:
        issues.append("RV32-Kerne (Ibex) haben kein MMU und koennen kein "
                      "Linux booten -- dafuer braucht es RV64.")
    if 64 in bits and 32 in bits:
        issues.append("RV32- und RV64-Kerne gemischt: das ergibt keinen "
                      "sinnvollen Linux-Chip.")
    if not l3_enabled:
        issues.append("Ohne L3/LLC fehlt die gemeinsame Cache-Ebene, die "
                      "Linux braucht.")
    if dram_mib < 256:
        issues.append("Weniger als 256 MiB DRAM sind fuer Linux zu knapp.")
    for mid, why in (("uart", "Serielle Konsole"),
                     ("blockdev", "Block-Geraet fuer das Dateisystem"),
                     ("spiflash", "Boot-Flash fuer Kernel/Initrd")):
        if mid not in modules:
            issues.append("%s fehlt (%s)." % (why, mid))
    return dict(
        ready=not issues,
        issues=issues,
        bits=sorted(bits),
        dram_mib=dram_mib,
        l3_enabled=l3_enabled,
    )


def modern_recommendations(cores, core_mix, modules, l3_enabled, l3_kib,
                           l3_banks, dram_mib, npu_enabled, gpu_enabled) -> list:
    """Konkrete Vorschlaege fuer einen modernen SoC-Aufbau."""
    recs = []

    def add(kind, target, label, why):
        recs.append(dict(kind=kind, target=target, label=label, why=why))

    if not l3_enabled:
        add("l3", True, "L3/LLC einschalten",
            "Moderne Prozessoren haben einen gemeinsamen Last-Level-Cache.")
    elif cores >= 4 and l3_kib < 1024:
        add("l3_kib", 1024, "L3 auf mindestens 1 MiB",
            "Ab vier Kernen lohnt sich ein groesserer gemeinsamer Cache.")
    if cores >= 4 and l3_banks < 2:
        add("l3_banks", 2, "L3 in 2 Banks",
            "Mehrere Banks erlauben gleichzeitige Zugriffe mehrerer Kerne.")
    if dram_mib < 1024:
        add("dram_mib", 1024, "1 GiB DRAM oder mehr",
            "Moderne Software und Linux brauchen Luft im Speicher.")
    for mid, why in (("dmi", "Debug-Zugriff"),
                     ("spiflash", "Boot-Flash"),
                     ("blockdev", "Speicher-Controller")):
        if mid not in modules:
            add("module", mid, "Modul %s ergaenzen" % mid, why)
    if cores >= 4 and not npu_enabled:
        add("npu", True, "NPU ergaenzen",
            "KI-/Matrixbeschleuniger sind in modernen SoCs Standard.")
    if not gpu_enabled:
        add("gpu", True, "GPU ergaenzen",
            "Grafik/Display gehoert bei modernen Prozessoren dazu.")
    return recs


def estimate(spec: dict) -> dict:
    """Vollstaendige Schaetzung aus einer Spec.

    spec = {
      "pdk": "sky130", "core_type": "rocket", "cores": 4,
      "l2_kib": 512, "modules": ["uart","gpio","dmi"], "rocc_count": 0,
      "activity": 1.0, "cycles_millions": 50, "build_system": "verilator"
    }
    """
    pdk_id = spec.get("pdk", "sky130")
    if pdk_id not in PRESETS:
        raise ValueError("Unbekannter Prozess: %r" % (pdk_id,))
    core_mix, cores, core_type = normalize_core_mix(spec)
    ct = CORE_TYPES[core_type]

    pdk = PRESETS[pdk_id]
    node = float(pdk["node_nm"])

    l3_enabled = bool(spec.get("l3_enabled", True))
    l3_kib = int(spec.get("l3_kib", spec.get("l2_kib", 512)))
    if l3_kib not in L3_SIZE_CHOICES:
        l3_kib = 512
    l3_ways = int(spec.get("l3_ways", 8))
    if l3_ways not in L3_WAYS_CHOICES:
        l3_ways = 8
    l3_banks = int(spec.get("l3_banks", 1))
    if l3_banks not in L3_BANK_CHOICES:
        l3_banks = 1

    gpu = GPUS_BY_ID.get(spec.get("gpu_id") or "") if spec.get("gpu_enabled") \
        else None
    gpu_count = 1 if gpu else 0

    npu_id = spec.get("npu_id") or "gemmini_16"
    npu = NPUS_BY_ID.get(npu_id) if spec.get("npu_enabled") else None
    if npu is None and spec.get("npu_enabled"):
        npu = NPUS[0]
    npu_count = 1 if npu else 0

    sim_partners = [p for p in (spec.get("sim_partners") or [])
                    if p in SIM_PARTNERS_BY_ID]
    dram_model = spec.get("dram_model", "simdram")
    if dram_model not in {m["id"] for m in DRAM_MODELS}:
        dram_model = "simdram"
    dram_mib = int(spec.get("dram_mib", 256))
    if dram_mib not in DRAM_SIZE_CHOICES:
        dram_mib = 256
    
    module_ids = list(spec.get("modules", []))
    rocc_count = max(0, int(spec.get("rocc_count", 0)))
    if rocc_count > 8:
        raise ValueError("Maximal 8 RoCC-Demo-Beschleuniger.")
    activity = float(spec.get("activity", 1.0))
    activity = min(max(activity, 0.05), 1.0)
    cycles_m = float(spec.get("cycles_millions", 50))

    # ---- Flaechen --------------------------------------------------------
    core_tile_areas = {
        kind: scaled_area(CORE_TYPES[kind]["area28"], node)
        for kind in core_mix
    }
    core_area_each = core_tile_areas[core_type]
    core_area_total = sum(core_tile_areas[kind] * count
                          for kind, count in core_mix.items())
    # L3/LLC: SRAM + Verzeichnis je Bank; aus -> nur ein Broadcast-Hub
    if l3_enabled:
        l3_area = scaled_area(L3_AREA28_PER_KIB * l3_kib +
                              L3_DIRECTORY_AREA28_PER_BANK * l3_banks, node)
    else:
        l3_area = scaled_area(NO_L3_HUB_AREA28, node)
    uncore_area = scaled_area(UNCORE_AREA28, node)
    rocc_area = scaled_area(ROCC_AREA28_EACH, node) * rocc_count
    gpu_area = scaled_area(gpu["area28"], node) * gpu_count if gpu else 0.0
    npu_area = scaled_area(npu["area28"], node) * npu_count if npu else 0.0

    module_blocks = []
    module_area_total = rocc_area
    module_report = []
    macs_total = 0

    if rocc_count:
        module_blocks.append(dict(
            key="rocc", label="%d x RoCC-Demo" % rocc_count, kind="accel",
            area=max(rocc_area * ROUTING_OVERHEAD, 1e-4), aspect=1.2))
        module_report.append(dict(id="rocc", label="%d x RoCC-Demo-Beschleuniger"
                                  % rocc_count, area_mm2=rocc_area,
                                  category="Beschleuniger", available=True))

    for mid in module_ids:
        mod = MODULES_BY_ID.get(mid)
        if mod is None:
            continue
        a = scaled_area(mod["area28"], node)
        module_area_total += a
        macs_total += int(mod.get("macs_per_cycle", 0) or 0)
        module_blocks.append(dict(
            key=mid, label=mod["label"],
            kind="accel" if mod.get("macs_per_cycle") else "periph",
            aspect=1.2 if mod.get("macs_per_cycle") else 2.0,
            area=max(a * ROUTING_OVERHEAD, 1e-4)))
        module_report.append(dict(
            id=mid, label=mod["label"], area_mm2=a, category=mod["category"],
            available=mod.get("available", True)))

    # Peripherie zu einem Block zusammenfassen (lesbarerer Floorplan)
    periph_area = sum(b["area"] for b in module_blocks if b["kind"] == "periph")
    module_blocks = [b for b in module_blocks if b["kind"] != "periph"]
    if periph_area > 0:
        module_blocks.append(dict(key="periph", label="Peripherie",
                                  kind="periph", area=periph_area, aspect=4.0))

    # NPU als eigener Block (Beschleuniger fuer KI/Matrix)
    if npu:
        macs_total += int(npu.get("macs_per_cycle", 0) or 0)
        module_blocks.append(dict(
            key="npu", label=npu["label"], kind="npu",
            area=max(npu_area * ROUTING_OVERHEAD, 1e-4), aspect=1.25))

    # GPU als eigener Block im Floorplan
    if gpu:
        module_blocks.append(dict(
            key="gpu", label=gpu["label"], kind="gpu",
            area=max(gpu_area * ROUTING_OVERHEAD, 1e-4), aspect=1.35))

    raw_area = (core_area_total + l3_area + uncore_area + module_area_total +
                gpu_area + npu_area)
    logic_area = raw_area * ROUTING_OVERHEAD

    floorplan_core_areas = {
        kind: area * ROUTING_OVERHEAD
        for kind, area in core_tile_areas.items()
    }
    fp = build_floorplan(
        cores, core_area_each * ROUTING_OVERHEAD,
        l3_area * ROUTING_OVERHEAD, module_blocks,
        uncore_area * ROUTING_OVERHEAD, core_mix, floorplan_core_areas,
        l3_label="L3 / LLC" if l3_enabled else "Coherence-Hub (keine L3)")
    die_area = fp["die_w"] * fp["die_h"]

    # ---- Takt & Leistung -------------------------------------------------
    f_used = min(CORE_TYPES[kind]["fmax_ghz"] for kind in core_mix)
    f_used = min(f_used, pdk["fmax_ghz"])
    f_ratio = f_used / pdk["fmax_ghz"] if pdk["fmax_ghz"] else 1.0
    p_dyn = pdk["power_w_per_mm2"] * die_area * f_ratio * activity
    p_leak = p_dyn * pdk["leak_frac"]
    p_total = p_dyn + p_leak

    # ---- Performance -----------------------------------------------------
    dmips = sum(count * CORE_TYPES[kind]["dmips_per_mhz"] *
                (f_used * 1000.0) for kind, count in core_mix.items())
    gflops_core = sum(count * CORE_TYPES[kind]["flops_per_cycle"] * f_used
                      for kind, count in core_mix.items())
    gflops_accel = macs_total * 2.0 * f_used
    tops_accel = gflops_accel / 1000.0
    gflops_gpu = 0.0
    if gpu:
        gflops_gpu = gpu["flops_per_cycle"] * gpu["fmax_ghz"] * gpu_count

    # ---- Wafer -----------------------------------------------------------
    side = math.sqrt(die_area)
    side = max(side, MIN_DIE_SIDE_MM)
    dpw = dies_per_wafer(side)
    y = murphy_yield(die_area, pdk["defect_per_cm2"])
    good = dpw * y
    wafer_area = math.pi * (WAFER_DIAMETER_MM / 2.0) ** 2

    # ---- Simulation ------------------------------------------------------
    sim = sim_estimate(cores, core_type, macs_total, cycles_m, core_mix,
                       dram_mib=dram_mib, dram_model=dram_model,
                       sim_partners=sim_partners, gpu_gflops=gflops_gpu)
    sim["build_system"] = spec.get("build_system", "verilator")

    assumptions = [
        "Flaechen sind auf 28 nm kalibriert und mit (nm/28)^%.1f skaliert "
        "(SRAM/Verdrahtung schrumpfen langsamer als Logik)." % AREA_SCALE_EXP,
        "%.0f %% Zuschlag fuer Verdrahtung, Takthueme und Whitespace."
        % ((ROUTING_OVERHEAD - 1.0) * 100),
        "Takt = min(Kern-Grenzfrequenz, Prozess-Grenzfrequenz) -- konservativ.",
        "Leistung = dynamische Leistungsdichte x Die-Flasche x f/fmax x "
        "Aktivitaet, plus Leckstrom.",
        "Yield nach Murphy, Wafer 300 mm, Saegegasse %.0f um."
        % (SCRIBE_MM * 1000),
        "Verilator-Durchsatz ~ %.1f Mcycles/s pro Rocket-Kern; jeder weitere "
        "Kern und schwerere Kerne reduzieren den Durchsatz."
        % 2.6,
        "SimDRAM kostet Durchsatz (Timing-Modell), SimAXIMem antwortet in "
        "einem Zyklus. Beide belegen den kompletten DRAM im Host-RAM.",
        "GPU-Werte sind Literatur-/Marktrichtwerte; die GPU hat in diesem "
        "Checkout kein Chipyard-Fragment und landet nicht im Verilog.",
    ]

    # ---- Linux-Faehigkeit und Empfehlungen -------------------------------
    linux = linux_readiness(core_mix, module_ids, l3_enabled, dram_mib)
    linux["target"] = bool(spec.get("linux_target", False))
    recommendations = modern_recommendations(
        cores, core_mix, module_ids, l3_enabled, l3_kib, l3_banks, dram_mib,
        bool(npu), bool(gpu))

    return dict(
        ok=True,
        spec=dict(pdk=pdk_id, core_type=core_type, core_mix=core_mix,
                  cores=cores, l2_kib=l3_kib,
                  l3_enabled=l3_enabled, l3_kib=l3_kib, l3_ways=l3_ways,
                  l3_banks=l3_banks,
                  gpu_enabled=bool(gpu), gpu_id=gpu["id"] if gpu else None,
                  gpu_count=gpu_count,
                  npu_enabled=bool(npu), npu_id=npu["id"] if npu else None,
                  npu_count=npu_count,
                  linux_target=bool(spec.get("linux_target", False)),
                  sim_partners=sim_partners, dram_model=dram_model,
                  dram_mib=dram_mib,
                  modules=module_ids,
                  rocc_count=rocc_count, activity=activity,
                  cycles_millions=cycles_m,
                  build_system=spec.get("build_system", "verilator")),
        pdk=dict(id=pdk_id, **pdk),
        core=dict(id=core_type, **ct),
        gpu=dict(gpu, area_mm2=gpu_area, gflops=gflops_gpu,
                 count=gpu_count) if gpu else None,
        npu=dict(npu, area_mm2=npu_area, count=npu_count) if npu else None,
        linux=linux,
        recommendations=recommendations,
        area=dict(
            core_each_mm2=core_area_each, core_total_mm2=core_area_total,
            l2_mm2=l3_area, l3_mm2=l3_area, gpu_mm2=gpu_area,
            npu_mm2=npu_area,
            uncore_mm2=uncore_area,
            modules_mm2=module_area_total, logic_raw_mm2=raw_area,
            logic_mm2=logic_area,
            floorplan_mm2=fp["die_area_content"],
            die_w_mm=fp["die_w"], die_h_mm=fp["die_h"], die_mm2=die_area,
            utilization=(logic_area / die_area) if die_area else 0.0,
        ),
        power=dict(freq_ghz=f_used, vdd=pdk["vdd"], dynamic_w=p_dyn,
                   leakage_w=p_leak, total_w=p_total,
                   power_density_w_per_mm2=(p_total / die_area) if die_area else 0.0),
        perf=dict(dmips=dmips, gflops_core=gflops_core,
                  gflops_accel=gflops_accel, tops_accel=tops_accel,
                  gflops_gpu=gflops_gpu, tops_gpu=gflops_gpu / 1000.0,
                  macs_total=macs_total,
                  npu_macs=int(npu.get("macs_per_cycle", 0)) if npu else 0,
                  dmips_per_mm2=(dmips / die_area) if die_area else 0.0),
        wafer=dict(diameter_mm=WAFER_DIAMETER_MM, die_side_mm=side,
                   dies_per_wafer=dpw, yield_pct=y, good_dies=good,
                   wafer_area_mm2=wafer_area,
                   utilization=(dpw * side * side / wafer_area) if wafer_area else 0.0),
        sim=sim,
        floorplan=fp,
        modules=module_report,
        assumptions=assumptions,
    )


def catalog() -> dict:
    """Katalog fuer das Frontend."""
    return dict(
        presets=[dict(id=k, **v) for k, v in PRESETS.items()],
        core_types=[dict(id=k, **v) for k, v in CORE_TYPES.items()],
        modules=MODULES,
        gpus=GPUS,
        npus=NPUS,
        sim_partners=SIM_PARTNERS,
        dram_models=DRAM_MODELS,
        l2_choices=L3_SIZE_CHOICES,
        area_ref_nm=AREA_REF_NM,
        area_scale_exp=AREA_SCALE_EXP,
        l3_choices=L3_SIZE_CHOICES,
        l3_ways_choices=L3_WAYS_CHOICES,
        l3_bank_choices=L3_BANK_CHOICES,
        dram_size_choices=DRAM_SIZE_CHOICES,
        linux_min_dram_mib=256,
        defaults=dict(pdk="sky130", core_type="rocket", cores=1,
                      l2_kib=512, l3_enabled=True, l3_kib=512, l3_ways=8,
                      l3_banks=1, gpu_enabled=False, gpu_id=None,
                      gpu_count=0, npu_enabled=False, npu_id="gemmini_16",
                      linux_target=False,
                      sim_partners=[p["id"] for p in SIM_PARTNERS
                                    if p.get("default")],
                      dram_model="simdram", dram_mib=256,
                      modules=[m["id"] for m in MODULES if m.get("default")],
                      rocc_count=0, activity=1.0, cycles_millions=50,
                      build_system="verilator"),
    )
