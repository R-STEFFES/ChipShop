"""Chipyard GUI -- Config-Rendering und Verilog-Export.

Aufgaben:
  * render_config(spec)  -> Scala-Quelltext der Chipyard-Config
  * write_config(...)    -> legt sie im Chipyard-Checkout ab
  * run_build(...)       -> ruft `make verilog` in sims/verilator auf,
                            schreibt das komplette Log in eine Datei
  * collect_verilog(...) -> sammelt die erzeugten .v/.sv/.f-Dateien ein
"""

from __future__ import annotations

import os
import shutil
import subprocess
import glob

import estimator

CHIPYARD_ROOT = os.environ.get("CHIPYARD_ROOT", "/home/raphael/chipyard")
CONFIG_CLASS = "ChipyardGuiConfig"
CONFIG_REL = "generators/chipyard/src/main/scala/config/ChipyardGuiConfigs.scala"
SIM_REL = "sims/verilator"
MODEL_PACKAGE = "chipyard.harness"
SIM_TOP = "TestHarness"
SIM_BINARY = "simulator-%s-%s" % (MODEL_PACKAGE, CONFIG_CLASS)
SIM_LONG_NAME = "%s.%s.%s" % (MODEL_PACKAGE, SIM_TOP, CONFIG_CLASS)

# conda-Umgebung von Chipyard
CONDA_SH = "/home/raphael/miniforge3/etc/profile.d/conda.sh"
CHIPYARD_ENV = os.path.join(CHIPYARD_ROOT, ".conda-env")

# Dateitypen, die aus dem Build eingesammelt werden.
RTL_EXT = (".sv", ".v", ".vh", ".conf", ".f", ".json")
# Grosse Zwischenartefakte nur auf Wunsch (include_fir=True).
EXTRA_EXT = (".fir", ".mlir")
EXTRA_SUFFIX = (".anno.json", ".mems.v", ".mems.conf")


# --------------------------------------------------------------------------
# Scala-Config rendern
# --------------------------------------------------------------------------

def _core_fragment(core_type: str, cores: int) -> str:
    ct = estimator.CORE_TYPES[core_type]
    return "new %s(%d)" % (ct["fragment"], cores)


def _l3_fragment(kib: int, ways: int) -> str:
    """Gemeinsamer, inklusiver Last-Level-Cache (Chipyards InclusiveCache)."""
    return ("new freechips.rocketchip.subsystem.WithInclusiveCache("
            "nWays = %d, capacityKB = %d)" % (ways, kib))


# Alte Bezeichnung beibehalten (wird von aussen/testweise benutzt).
_l2_fragment = _l3_fragment


def render_config(spec: dict) -> dict:
    """Erzeugt den Scala-Quelltext. Liefert dict(source, fragments, warnings)."""
    fragments = []
    warnings = []

    core_mix, cores, _core_type = estimator.normalize_core_mix(spec)
    modules = list(spec.get("modules", []))
    rocc = max(0, int(spec.get("rocc_count", 0)))

    # --- L3 / LLC ---------------------------------------------------------
    l3_enabled = bool(spec.get("l3_enabled", True))
    l3_kib = int(spec.get("l3_kib", spec.get("l2_kib", 512)))
    if l3_kib not in estimator.L3_SIZE_CHOICES:
        l3_kib = 512
    l3_ways = int(spec.get("l3_ways", 8))
    if l3_ways not in estimator.L3_WAYS_CHOICES:
        l3_ways = 8
    l3_banks = int(spec.get("l3_banks", 1))
    if l3_banks not in estimator.L3_BANK_CHOICES:
        l3_banks = 1

    # --- GPU --------------------------------------------------------------
    gpu = None
    if spec.get("gpu_enabled") and spec.get("gpu_id"):
        gpu = estimator.GPUS_BY_ID.get(spec["gpu_id"])
        if gpu is None:
            warnings.append("Unbekannte GPU %r wird ignoriert."
                            % spec.get("gpu_id"))
    gpu_frag = (gpu or {}).get("fragment")

    # --- NPU (Gemmini / NVDLA) --------------------------------------------
    npu = None
    if spec.get("npu_enabled"):
        npu = estimator.NPUS_BY_ID.get(spec.get("npu_id") or "",
                                       estimator.NPUS[0])

    # --- Simulationspartner (Harness) -------------------------------------
    sim_partners = [p for p in (spec.get("sim_partners") or [])
                    if p in estimator.SIM_PARTNERS_BY_ID]
    dram_model = spec.get("dram_model", "simdram")
    if dram_model not in {m["id"] for m in estimator.DRAM_MODELS}:
        dram_model = "simdram"
    dram_mib = int(spec.get("dram_mib", 256))
    if dram_mib not in estimator.DRAM_SIZE_CHOICES:
        dram_mib = 256
    dram_latency = max(0, min(int(spec.get("dram_latency", 0) or 0), 64))

    harness_lines = []
    for pid in sim_partners:
        partner = estimator.SIM_PARTNERS_BY_ID[pid]
        missing = [r for r in partner.get("requires", [])
                   if r not in modules]
        if missing:
            warnings.append(
                "%s braucht das SoC-Modul %s -- der Simulationspartner wird "
                "nicht eingebunden." % (partner["label"], ", ".join(missing)))
            continue
        if pid == "dram":
            continue                      # wird unten gesondert behandelt
        if partner.get("in_base"):
            # Steckt bereits in AbstractConfig. Nur Parameter-Aenderungen
            # (z. B. beschreibbares Flash) brauchen ein eigenes Fragment.
            if pid == "spiflash" and spec.get("spiflash_rw"):
                harness_lines.append(
                    "new %s(rdOnly = false)" % partner["fragment"])
            continue
        if not partner.get("fragment"):
            continue
        harness_lines.append("new %s" % partner["fragment"])

    if dram_model == "aximem":
        harness_lines.append("new %s" % estimator.DRAM_MODELS[1]["fragment"])
    elif dram_latency > 0:
        harness_lines.append(
            "new %s(additionalLatency = %d)"
            % (estimator.DRAM_MODELS[0]["fragment"], dram_latency))

    dram_bytes = dram_mib * 1024 * 1024
    harness_lines.append(
        "new freechips.rocketchip.subsystem.WithExtMemSize(BigInt(%d))"
        % dram_bytes)

    uses_buildrocc = False

    # --- Module: Beschleuniger zuerst (brechen BuildRoCC), dann Peripherie -
    accel_lines = []
    periph_lines = []
    selected = set(modules)

    # Module, die ohnehin im Basis-Config (AbstractConfig) stecken, duerfen
    # NICHT erneut als Fragment auftauchen -- das legt das Geraet ein zweites
    # Mal an und fuehrt zu Adresskonflikten ("Ports cannot overlap").
    # Nur beim Abwaehlen muss das Basis-Geraet explizit entfernt werden.
    in_base_removals = []
    for mod in estimator.MODULES:
        if not mod.get("in_base"):
            continue
        if mod["id"] not in selected and mod.get("remove_fragment"):
            in_base_removals.append("new %s" % mod["remove_fragment"])

    for mid in modules:
        mod = estimator.MODULES_BY_ID.get(mid)
        if mod is None:
            warnings.append("Unbekanntes Modul %r wird ignoriert." % mid)
            continue
        if mod.get("in_base"):
            continue
        if not mod.get("available", True) or not mod.get("fragment"):
            warnings.append("%s hat in diesem Checkout kein nutzbares Fragment "
                            "und wird nur in der Schaetzung beruecksichtigt."
                            % mod["label"])
            continue
        line = "new %s" % mod["fragment"]
        if mid == "gemmini":
            accel_lines.append(line)
            uses_buildrocc = True
        else:
            periph_lines.append(line)

    fragments.extend(accel_lines)

    # --- NPU: Gemmini/NVDLA haengen am RoCC (BuildRoCC) -------------------
    if npu:
        fragments.append("new %s" % npu["fragment"])
        uses_buildrocc = True

    # --- Simulationspartner zuerst: links stehende Harness-Binder haben
    #     Vorrang vor den Basis-Bindern aus AbstractConfig. -----------------
    fragments.extend(harness_lines)

    # --- GPU --------------------------------------------------------------
    if gpu:
        if gpu_frag:
            fragments.append("new %s" % gpu_frag)
        else:
            warnings.append(
                "%s hat in diesem Chipyard-Checkout kein Fragment und wird nur "
                "in der Schaetzung beruecksichtigt (nicht im Verilog)."
                % gpu["label"])

    # --- RoCC-Demo-Beschleuniger ------------------------------------------
    if rocc > 0:
        if rocc > cores:
            warnings.append("RoCC-Instanzen auf %d begrenzt (nur %d Kerne)."
                            % (cores, cores))
            rocc = cores
        harts = ", ".join(str(i) for i in range(rocc))
        fragments.append("new chipyard.config.WithMultiRoCC")
        fragments.append("new chipyard.config.WithMultiRoCCFromBuildRoCC(%s)" % harts)
        fragments.append("new chipyard.config.WithAccumulatorRoCC")
        uses_buildrocc = True

    # --- Kerne: eine Scala-Fragment-Instanz je ausgewaehltem Typ ------------
    core_bits = set()
    for kind, count in core_mix.items():
        fragments.append(_core_fragment(kind, count))
        ct = estimator.CORE_TYPES[kind]
        core_bits.add(ct.get("bits", 64))
        for extra in ct.get("extra_fragments", []):
            line = "new %s" % extra
            if line not in fragments:
                fragments.append(line)
    if len(core_bits) > 1:
        warnings.append("RV32- und RV64-Kerne gemischt: das ergibt keinen "
                        "sinnvollen Chip (verschiedene Befehlssaetze).")

    # --- L3 / LLC ---------------------------------------------------------
    if l3_enabled:
        fragments.append(_l3_fragment(l3_kib, l3_ways))
        if l3_banks > 1:
            # Muss rechts von WithInclusiveCache stehen: dort wird nBanks
            # gelesen, um die Sets je Bank zu berechnen.
            fragments.append(
                "new freechips.rocketchip.subsystem.WithNBanks(%d)" % l3_banks)
    else:
        # Kein LLC: nur ein Broadcast-Hub haelt die Kohaerenz.
        fragments.append("new chipyard.config.WithBroadcastManager")

    # --- Peripherie -------------------------------------------------------
    fragments.extend(in_base_removals)
    fragments.extend(periph_lines)

    # --- Systembus-Breite bei Beschleunigern ------------------------------
    if uses_buildrocc:
        fragments.append("new chipyard.config.WithSystemBusWidth(128)")

    # --- Basis zuletzt (niedrigste Prioritaet) ----------------------------
    fragments.append("new chipyard.config.AbstractConfig")

    body = "\n  ".join(f + " ++" for f in fragments[:-1])
    body = "  " + body + "\n  " + fragments[-1]

    imports = [
        "package chipyard",
        "",
        "import org.chipsalliance.cde.config.Config",
    ]

    core_summary = ", ".join("%d x %s" % (count, kind)
                              for kind, count in core_mix.items())
    l3_summary = ("%d KiB, %d-fach, %d Bank(s)" % (l3_kib, l3_ways, l3_banks)
                  if l3_enabled else "aus (Broadcast-Hub)")
    spec_comment = (
        "// AUTO-GENERIERT von chipyard-gui (" + CONFIG_CLASS + ")\n"
        "// NICHT manuell editieren -- wird beim naechsten Generieren "
        "ueberschrieben.\n"
        "//\n"
        "//   Kerne     : %s\n"
        "//   L3 / LLC  : %s\n"
        "//   GPU       : %s\n"
        "//   NPU       : %s\n"
        "//   Module    : %s\n"
        "//   Sim-Partner: %s\n"
        "//   DRAM      : %d MiB (%s)\n"
        "//   Linux-Ziel: %s\n"
        "//   Prozess   : %s\n"
        % (core_summary, l3_summary,
           gpu["label"] if gpu else "-",
           npu["label"] if npu else "-",
           ", ".join(modules) or "-",
           ", ".join(sim_partners) or "-", dram_mib, dram_model,
           "ja" if spec.get("linux_target") else "nein",
           spec.get("pdk", "sky130"))
    )

    source = "\n".join(imports) + "\n\n" + spec_comment + \
        "\nclass %s extends Config(\n%s)\n" % (CONFIG_CLASS, body)

    return dict(source=source, fragments=fragments, warnings=warnings,
                config_class=CONFIG_CLASS)


# --------------------------------------------------------------------------
# Dateien / Build
# --------------------------------------------------------------------------

def _config_body(source: str) -> str:
    """Config ohne Kommentarzeilen -- fuer den 'hat sich wirklich geaendert?'-Test."""
    return "\n".join(line for line in source.splitlines()
                     if not line.lstrip().startswith("//")).strip()


def config_would_change(root: str, source: str) -> bool:
    """True, wenn der Scala-Code sich vom Checkout unterscheidet."""
    path = os.path.join(root, CONFIG_REL)
    try:
        with open(path, "r", errors="replace") as fh:
            return _config_body(fh.read()) != _config_body(source)
    except OSError:
        return True


def write_config(root: str, source: str) -> str:
    """Schreibt die Config ins Checkout -- nur wenn sich der Code geaendert hat.

    Chipyard baut RTL und Simulator neu, sobald der Zeitstempel der Config
    neuer ist. Reine Kommentaraenderungen (z. B. eine andere GPU im Kopf)
    duerfen deshalb keinen Rebuild ausloesen.
    """
    path = os.path.join(root, CONFIG_REL)
    if not config_would_change(root, source):
        return path
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fh:
        fh.write(source)
    return path


def conda_available() -> bool:
    return os.path.isfile(CONDA_SH) and os.path.isdir(CHIPYARD_ENV)


def build_script(root: str, jobs: int = 4) -> str:
    return (
        "#!/usr/bin/env bash\n"
        "set -o pipefail\n"
        'source "%(conda)s"\n'
        'conda activate "%(env)s"\n'
        'cd "%(root)s/%(sim)s"\n'
        'exec make verilog CONFIG=%(cfg)s -j%(jobs)d\n'
        % dict(conda=CONDA_SH, env=CHIPYARD_ENV, root=root, sim=SIM_REL,
               cfg=CONFIG_CLASS, jobs=jobs)
    )


def run_build(root: str, log_path: str, jobs: int = 4) -> int:
    """Fuehrt den Verilog-Build aus und schreibt stdout+stderr ins Log."""
    script = os.path.join(os.path.dirname(log_path), "build.sh")
    with open(script, "w") as fh:
        fh.write(build_script(root, jobs))
    os.chmod(script, 0o755)

    with open(log_path, "w") as log:
        log.write("$ bash %s\n" % script)
        log.flush()
        if not conda_available():
            log.write("\n::ERROR:: conda/conda-env nicht gefunden "
                      "(erwartet: %s / %s)\n" % (CONDA_SH, CHIPYARD_ENV))
            return 127
        proc = subprocess.run(["bash", script], stdout=log,
                              stderr=subprocess.STDOUT)
        return proc.returncode


# --------------------------------------------------------------------------
# C++-Simulation (Verilator): bauen und ausfuehren
# --------------------------------------------------------------------------

def simulator_path(root: str) -> str:
    return os.path.join(root, SIM_REL, SIM_BINARY)


def simulator_built(root: str) -> bool:
    return os.path.isfile(simulator_path(root))


def _conda_lines() -> str:
    return ('source "%s"\nconda activate "%s"\n' % (CONDA_SH, CHIPYARD_ENV))


def sim_build_script(root: str, jobs: int = 4) -> str:
    """Uebersetzt RTL + C++-Simulator (`make` ohne Ziel = Standardziel)."""
    return (
        "#!/usr/bin/env bash\n"
        "set -o pipefail\n"
        + _conda_lines() +
        'cd "%(root)s/%(sim)s"\n'
        'make CONFIG=%(cfg)s -j%(jobs)d\n'
        % dict(root=root, sim=SIM_REL, cfg=CONFIG_CLASS, jobs=jobs)
    )


def sim_run_script(root: str, workload: str, sim_flags: str = "",
                   timeout_s: int = 3600, jobs: int = 4) -> str:
    """Startet die fertige Simulation mit einem Programm (`run-binary-fast`)."""
    import shlex
    flags = " ".join(shlex.quote(f) for f in (sim_flags or "").split())
    return (
        "#!/usr/bin/env bash\n"
        "set -o pipefail\n"
        + _conda_lines() +
        'cd "%(root)s/%(sim)s"\n'
        'exec timeout --signal=INT --kill-after=10 %(timeout)d '
        'make CONFIG=%(cfg)s BREAK_SIM_PREREQ=1 run-binary-fast '
        'BINARY=%(bin)s EXTRA_SIM_FLAGS=%(flags)s\n'
        % dict(root=root, sim=SIM_REL, cfg=CONFIG_CLASS,
               bin=shlex.quote(workload), flags=shlex.quote(flags),
               timeout=max(10, int(timeout_s)))
    )


def _run_script(script: str, log_path: str, append: bool) -> int:
    """Fuehrt ein Shell-Skript aus und schreibt die Ausgabe fortlaufend ins Log."""
    os.chmod(script, 0o755)
    mode = "a" if append else "w"
    with open(log_path, mode) as log:
        log.write("$ bash %s\n" % script)
        log.flush()
        if not conda_available():
            log.write("\n::ERROR:: conda/conda-env nicht gefunden "
                      "(erwartet: %s / %s)\n" % (CONDA_SH, CHIPYARD_ENV))
            return 127
        proc = subprocess.run(["bash", script], stdout=log,
                              stderr=subprocess.STDOUT)
        return proc.returncode


def build_simulator(root: str, log_path: str, jobs: int = 4) -> int:
    """Baut den C++-Simulator (inkl. RTL) und schreibt ins Log."""
    script = os.path.join(os.path.dirname(log_path), "sim-build.sh")
    with open(script, "w") as fh:
        fh.write(sim_build_script(root, jobs))
    return _run_script(script, log_path, append=False)


def run_simulation(root: str, workload: str, log_path: str,
                   sim_flags: str = "", timeout_s: int = 3600,
                   jobs: int = 4) -> int:
    """Startet die Simulation und haengt die Ausgabe an das Log an."""
    script = os.path.join(os.path.dirname(log_path), "sim-run.sh")
    with open(script, "w") as fh:
        fh.write(sim_run_script(root, workload, sim_flags, timeout_s, jobs))
    return _run_script(script, log_path, append=True)


def sim_output_dir(root: str) -> str:
    return os.path.join(root, SIM_REL, "output", SIM_LONG_NAME)


def collect_sim_output(root: str, dest: str) -> dict:
    """Sammelt Simulations-Logs/Ausgaben aus sims/verilator/output/."""
    src = sim_output_dir(root)
    os.makedirs(dest, exist_ok=True)
    copied = []
    if os.path.isdir(src):
        for dirpath, _dirs, files in os.walk(src):
            for fname in sorted(files):
                spath = os.path.join(dirpath, fname)
                rel = os.path.relpath(spath, src)
                dpath = os.path.join(dest, rel)
                os.makedirs(os.path.dirname(dpath) or dest, exist_ok=True)
                try:
                    shutil.copy2(spath, dpath)
                except OSError:
                    continue
                copied.append(rel)
    total = sum(os.path.getsize(os.path.join(dest, f))
                for f in copied if os.path.isfile(os.path.join(dest, f)))
    return dict(found=bool(copied), dir=src, files=copied, total_bytes=total)


def list_workloads(root: str) -> list:
    """Fertige RISC-V-Programme im Checkout, die sich direkt simulieren lassen."""
    isa_dir = os.path.join(
        root, ".conda-env/riscv-tools/riscv64-unknown-elf/share/riscv-tests/isa")
    patterns = [
        ".conda-env/riscv-tools/riscv64-unknown-elf/share/riscv-tests/"
        "benchmarks/*.riscv",
        "generators/riscv-sodor/riscv-bmarks/*.riscv",
        "tests/build/*.riscv",
        "outputs/workloads/*.riscv",
    ]
    skip_ext = (".dump", ".py", ".c", ".h", ".sh", ".json", ".md", ".txt")
    found = {}

    def add(path, group):
        name = os.path.basename(path)
        if name in found or name.lower().endswith(skip_ext):
            return
        # Nur RV64-Programme: die Kerne in Chipyard sind RV64GC.
        if group == "RISC-V-Tests (ISA)" and not (
                name.startswith("rv64") or name.startswith("hypervisor-")):
            return
        try:
            size = os.path.getsize(path)
        except OSError:
            return
        found[name] = dict(name=name, path=path, size=size, group=group)

    for pattern in patterns:
        for path in glob.glob(os.path.join(root, pattern)):
            if "/isa/" in path:
                add(path, "RISC-V-Tests (ISA)")
            elif "/benchmarks/" in path:
                add(path, "Benchmarks")
            elif "/tests/" in path:
                add(path, "Chipyard-Tests")
            else:
                add(path, "Sonstige")
    # ISA-Tests liegen ohne Dateiendung im Checkout (rv64ui-p-add usw.)
    if os.path.isdir(isa_dir):
        for path in sorted(glob.glob(os.path.join(isa_dir, "*"))):
            if os.path.isfile(path):
                add(path, "RISC-V-Tests (ISA)")
    return sorted(found.values(), key=lambda w: (w["group"], w["name"]))



def generated_dir(root: str) -> str:
    base = os.path.join(root, SIM_REL, "generated-src")
    target = "%s.%s.%s" % (MODEL_PACKAGE, SIM_TOP, CONFIG_CLASS)
    direct = os.path.join(base, target)
    if os.path.isdir(direct):
        return direct
    # Notfalls suchen (Modellpaket kann je nach Chipyard-Version abweichen)
    hits = glob.glob(os.path.join(base, "*%s" % CONFIG_CLASS))
    hits = [h for h in hits if os.path.isdir(h)]
    return hits[0] if hits else direct


def collect_verilog(root: str, dest: str, include_fir: bool = False) -> dict:
    """Sammelt die generierten Verilog-Dateien nach dest.

    Die eigentliche RTL liegt in <build_dir>/gen-collateral (firtool-Ausgabe),
    die Dateilisten/Annotations liegen eine Ebene hoeher im Build-Verzeichnis.
    Beides wird uebernommen -- nur die oberste Ebene einzusammeln wuerde die
    .sv-Dateien komplett verpassen.
    """
    build = generated_dir(root)
    collateral = os.path.join(build, "gen-collateral")
    os.makedirs(dest, exist_ok=True)

    if not os.path.isdir(build):
        return dict(found=False, dir=build, source_dir=build, rtl_dir=collateral,
                    files=[], total_bytes=0, rtl_files=0)

    exts = list(RTL_EXT) + (list(EXTRA_EXT) if include_fir else [])

    def wanted(name: str) -> bool:
        low = name.lower()
        return low.endswith(tuple(exts)) or low.endswith(EXTRA_SUFFIX)

    copied = []

    # 1) firtool-Ausgabe (die RTL) inkl. Unterordner (z.B. metadata/)
    if os.path.isdir(collateral):
        for dirpath, _dirnames, filenames in os.walk(collateral):
            for fname in sorted(filenames):
                if not wanted(fname):
                    continue
                spath = os.path.join(dirpath, fname)
                rel = os.path.relpath(spath, collateral)
                dpath = os.path.join(dest, rel)
                os.makedirs(os.path.dirname(dpath), exist_ok=True)
                shutil.copy2(spath, dpath)
                copied.append(rel)

    # 2) Dateilisten / Konfigurationen aus der Build-Ebene
    for fname in sorted(os.listdir(build)):
        spath = os.path.join(build, fname)
        if not os.path.isfile(spath) or not wanted(fname):
            continue
        shutil.copy2(spath, os.path.join(dest, fname))
        copied.append(fname)

    total = sum(os.path.getsize(os.path.join(dest, f)) for f in copied)
    rtl = [f for f in copied if f.lower().endswith((".sv", ".v", ".vh"))]
    return dict(found=bool(copied), dir=build, source_dir=build,
                rtl_dir=collateral, files=copied, total_bytes=total,
                rtl_files=len(rtl))


def chipyard_info(root: str) -> dict:
    """Kurzinfo zum Chipyard-Checkout fuer die GUI."""
    info = dict(root=root, exists=os.path.isdir(root),
                conda=conda_available(), path=CONFIG_REL,
                config_class=CONFIG_CLASS)
    try:
        out = subprocess.run(
            ["git", "-C", root, "describe", "--tags", "--always"],
            capture_output=True, text=True, timeout=10)
        info["version"] = out.stdout.strip() or "unbekannt"
    except Exception:
        info["version"] = "unbekannt"
    return info
