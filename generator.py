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


def _l2_fragment(l2_kib: int) -> str:
    nways = max(2, l2_kib // 64)
    return ("new freechips.rocketchip.subsystem.WithInclusiveCache("
            "nWays = %d, capacityKB = %d)" % (nways, l2_kib))


def render_config(spec: dict) -> dict:
    """Erzeugt den Scala-Quelltext. Liefert dict(source, fragments, warnings)."""
    fragments = []
    warnings = []

    core_type = spec.get("core_type", "rocket")
    cores = int(spec.get("cores", 1))
    modules = list(spec.get("modules", []))
    rocc = max(0, int(spec.get("rocc_count", 0)))
    l2_kib = int(spec.get("l2_kib", 512))

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

    # --- Kerne ------------------------------------------------------------
    fragments.append(_core_fragment(core_type, cores))

    # --- L2 / LLC ---------------------------------------------------------
    fragments.append(_l2_fragment(l2_kib))

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

    spec_comment = (
        "// AUTO-GENERIERT von chipyard-gui (" + CONFIG_CLASS + ")\n"
        "// NICHT manuell editieren -- wird beim naechsten Generieren "
        "ueberschrieben.\n"
        "//\n"
        "//   Kerne     : %d x %s\n"
        "//   L2 / LLC  : %d KiB\n"
        "//   Module    : %s\n"
        "//   Prozess   : %s\n"
        % (cores, core_type, l2_kib, ", ".join(modules) or "-",
           spec.get("pdk", "sky130"))
    )

    source = "\n".join(imports) + "\n\n" + spec_comment + \
        "\nclass %s extends Config(\n%s)\n" % (CONFIG_CLASS, body)

    return dict(source=source, fragments=fragments, warnings=warnings,
                config_class=CONFIG_CLASS)


# --------------------------------------------------------------------------
# Dateien / Build
# --------------------------------------------------------------------------

def write_config(root: str, source: str) -> str:
    path = os.path.join(root, CONFIG_REL)
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
