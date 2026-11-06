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
        uarch="in-order, 1-wide, FPU (Zfh/Zfinx)",
        fragment="freechips.rocketchip.rocket.WithNHugeCores",
    ),
    "boom_small": dict(
        label="BOOM - Small -- out-of-order", area28=0.85,
        dmips_per_mhz=2.6, fmax_ghz=1.20, flops_per_cycle=2.0, l1_kib=32,
        uarch="OoO, 2-wide decode, 1 Branch-Unit",
        fragment="boom.v4.common.WithNSmallBooms",
    ),
    "boom_medium": dict(
        label="BOOM - Medium -- out-of-order", area28=1.65,
        dmips_per_mhz=3.4, fmax_ghz=1.10, flops_per_cycle=2.0, l1_kib=48,
        uarch="OoO, 3-wide decode, 2 Branch-Units",
        fragment="boom.v4.common.WithNMediumBooms",
    ),
    "boom_large": dict(
        label="BOOM - Large -- out-of-order", area28=3.20,
        dmips_per_mhz=4.2, fmax_ghz=1.00, flops_per_cycle=2.0, l1_kib=64,
        uarch="OoO, 4-wide decode, 3 Branch-Units",
        fragment="boom.v4.common.WithNLargeBooms",
    ),
}


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

    # ---- Beschleuniger (echte Fragmente) ---------------------------------
    dict(id="gemmini", label="Gemmini -- GEMM-Array", category="Beschleuniger",
         area28=1.60, fragment="gemmini.DefaultGemminiConfig",
         macs_per_cycle=256, default=False,
         note="Systolischer 16x16-Array, 256 MACs/Zyklus, an Hart 0"),

    # ---- nur Schaetzung (kein Fragment in diesem Checkout) ---------------
    dict(id="nvdla", label="NVDLA -- CNN-Beschleuniger",
         category="Beschleuniger (nur Schaetzung)", area28=15.00,
         fragment=None, available=False, macs_per_cycle=1024, default=False,
         note="NVIDIA Deep-Learning-Accelerator, Large-Konfiguration"),
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
L2_AREA28_PER_KIB = 0.00215   # inklusiver L2 (512 KiB ~ 1.10 mm2 @ 28 nm)
L2_SIZE_CHOICES = [128, 256, 512, 1024]

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

    if target_w is None:
        total = sum(i["area"] for i in items)
        target_w = math.sqrt(total * 1.25)

    x = y = row_h = 0.0
    width = 0.0
    for it in items:
        if x > 0.0 and x + it["w"] > target_w:
            x = 0.0
            y += row_h
            row_h = 0.0
        it["x"], it["y"] = x, y
        x += it["w"]
        row_h = max(row_h, it["h"])
        width = max(width, x)

    height = y + row_h
    return items, width, height


def build_floorplan(cores: int, core_area_mm2: float, l2_area_mm2: float,
                    module_blocks, uncore_area: float):
    """Baut den groben Floorplan.

    Kern-Cluster wird als Grid vorab verdichtet (anschaulicher als eine Reihe),
    danach werden Cluster/L2/Uncore/Module per Shelf-Packing gesetzt.
    """
    blocks = []

    # --- Kern-Cluster als Grid --------------------------------------------
    if cores > 0:
        cols = int(math.ceil(math.sqrt(cores)))
        rows = int(math.ceil(cores / float(cols)))
        cw = ch = math.sqrt(core_area_mm2 * 1.12)   # 12 % Overhead je Kachel
        cluster_w, cluster_h = cols * cw, rows * ch
        blocks.append(dict(
            key="cores", label="%d x %s" % (cores, "Kern"),
            kind="cores", area=cluster_w * cluster_h,
            aspect=cluster_w / cluster_h if cluster_h else 1.0,
            cores=cores, cols=cols, rows=rows, tile=core_area_mm2,
        ))

    # --- L2 / LLC ---------------------------------------------------------
    if l2_area_mm2 > 0:
        blocks.append(dict(key="l2", label="L2 / LLC", kind="memory",
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
                 cycles_millions: float):
    """Grober Abschaetzung des Host-Aufwands fuer eine Simulation."""
    ct = CORE_TYPES[core_type]
    # Verilator-Durchsatz: 1 Rocket-Kern ~ 2.6 Mcycles/s auf einem modernen
    # Host-Kern; jeder weitere Kern kostet, schwere Kerne kosten mehr.
    base_throughput = 2.6            # Mcycles/s, 1 Rocket
    core_cost = ct["area28"] / CORE_TYPES["rocket"]["area28"]
    throughput = base_throughput / (1.0 + 0.55 * max(0, cores - 1)) / core_cost
    throughput = max(throughput, 0.02)

    wall_h = cycles_millions / throughput / 3600.0

    ram_gb = 2.5 + 0.8 * cores + (0.5 if accel_mac_total > 0 else 0.0)
    build_min = 6.0 + 1.8 * cores + (4.0 if accel_mac_total > 0 else 0.0)

    return dict(
        cycles_millions=cycles_millions,
        verilator_throughput_mcyc_s=throughput,
        verilator_wall_hours=wall_h,
        host_ram_gb=ram_gb,
        build_minutes_estimate=build_min,
        firesim_sim_mhz=100.0,
        firesim_wall_hours=cycles_millions / (100.0 * 3600.0 / 1000.0),
        note=("Verilator ist software-emuliert und langsam aber guenstig; "
              "FireSim nutzt eine FPGA-Karte und laeuft ~1000x schneller, "
              "braucht aber Cloud-/FPGA-Hardware."),
    )


# --------------------------------------------------------------------------
# Hauptfunktion
# --------------------------------------------------------------------------

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
    core_type = spec.get("core_type", "rocket")
    if core_type not in CORE_TYPES:
        raise ValueError("Unbekannter Kern-Typ: %r" % (core_type,))

    pdk = PRESETS[pdk_id]
    ct = CORE_TYPES[core_type]
    node = float(pdk["node_nm"])

    cores = int(spec.get("cores", 1))
    if cores < 1:
        raise ValueError("Mindestens 1 Kern noetig.")
    if cores > 64:
        raise ValueError("Maximal 64 Kerne (darueber ist die Naeherung unrelevant).")

    l2_kib = int(spec.get("l2_kib", 512))
    if l2_kib not in L2_SIZE_CHOICES:
        l2_kib = 512

    module_ids = list(spec.get("modules", []))
    rocc_count = max(0, int(spec.get("rocc_count", 0)))
    if rocc_count > 8:
        raise ValueError("Maximal 8 RoCC-Demo-Beschleuniger.")
    activity = float(spec.get("activity", 1.0))
    activity = min(max(activity, 0.05), 1.0)
    cycles_m = float(spec.get("cycles_millions", 50))

    # ---- Flaechen --------------------------------------------------------
    core_area_each = scaled_area(ct["area28"], node)
    core_area_total = core_area_each * cores
    l2_area = scaled_area(L2_AREA28_PER_KIB * l2_kib, node)
    uncore_area = scaled_area(UNCORE_AREA28, node)
    rocc_area = scaled_area(ROCC_AREA28_EACH, node) * rocc_count

    module_blocks = []
    module_area_total = rocc_area
    module_report = []
    macs_total = 0

    if rocc_count:
        module_blocks.append(dict(
            key="rocc", label="%d x RoCC-Demo" % rocc_count, kind="accel",
            area=max(rocc_area, 1e-4), aspect=1.2))
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
            key=mid, label=mod["label"], kind="accel" if mod.get("macs_per_cycle")
            else "periph", area=max(a, 1e-4),
            aspect=1.2 if mod.get("macs_per_cycle") else 2.0))
        module_report.append(dict(
            id=mid, label=mod["label"], area_mm2=a, category=mod["category"],
            available=mod.get("available", True)))

    # Peripherie zu einem Block zusammenfassen (lesbarerer Floorplan)
    periph_area = sum(b["area"] for b in module_blocks if b["kind"] == "periph")
    module_blocks = [b for b in module_blocks if b["kind"] != "periph"]
    if periph_area > 0:
        module_blocks.append(dict(key="periph", label="Peripherie",
                                  kind="periph", area=periph_area, aspect=4.0))

    raw_area = core_area_total + l2_area + uncore_area + module_area_total
    logic_area = raw_area * ROUTING_OVERHEAD

    fp = build_floorplan(cores, core_area_each, l2_area, module_blocks,
                         uncore_area)
    die_area = fp["die_w"] * fp["die_h"]

    # ---- Takt & Leistung -------------------------------------------------
    f_used = min(ct["fmax_ghz"], pdk["fmax_ghz"])     # konservativ
    f_ratio = f_used / pdk["fmax_ghz"] if pdk["fmax_ghz"] else 1.0
    p_dyn = pdk["power_w_per_mm2"] * die_area * f_ratio * activity
    p_leak = p_dyn * pdk["leak_frac"]
    p_total = p_dyn + p_leak

    # ---- Performance -----------------------------------------------------
    dmips = cores * ct["dmips_per_mhz"] * (f_used * 1000.0)
    gflops_core = cores * ct["flops_per_cycle"] * f_used
    gflops_accel = macs_total * 2.0 * f_used
    tops_accel = gflops_accel / 1000.0

    # ---- Wafer -----------------------------------------------------------
    side = math.sqrt(die_area)
    side = max(side, MIN_DIE_SIDE_MM)
    dpw = dies_per_wafer(side)
    y = murphy_yield(die_area, pdk["defect_per_cm2"])
    good = dpw * y
    wafer_area = math.pi * (WAFER_DIAMETER_MM / 2.0) ** 2

    # ---- Simulation ------------------------------------------------------
    sim = sim_estimate(cores, core_type, macs_total, cycles_m)
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
    ]

    return dict(
        ok=True,
        spec=dict(pdk=pdk_id, core_type=core_type, cores=cores, l2_kib=l2_kib,
                  modules=module_ids, rocc_count=rocc_count,
                  activity=activity, cycles_millions=cycles_m,
                  build_system=spec.get("build_system", "verilator")),
        pdk=dict(id=pdk_id, **pdk),
        core=dict(id=core_type, **ct),
        area=dict(
            core_each_mm2=core_area_each, core_total_mm2=core_area_total,
            l2_mm2=l2_area, uncore_mm2=uncore_area,
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
                  dmips_per_mm2=(dmips / die_area) if die_area else 0.0,
                  macs_total=macs_total),
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
        l2_choices=L2_SIZE_CHOICES,
        defaults=dict(pdk="sky130", core_type="rocket", cores=1, l2_kib=512,
                      modules=[m["id"] for m in MODULES if m.get("default")],
                      rocc_count=0, activity=1.0, cycles_millions=50,
                      build_system="verilator"),
    )
