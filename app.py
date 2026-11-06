"""Chipyard GUI -- Flask-Backend.

Drei Aufgaben:
  1. /api/estimate   -- grobe Vorab-Schaetzung (Flaesche, Leistung, Wafer, Sim)
  2. /api/config     -- erzeugt die Scala-Chipyard-Config (und legt sie ab)
  3. /api/build      -- ruft `make verilog` auf und liefert die Verilog-Dateien

Der Build laeuft in einem Hintergrund-Thread; der Fortschritt wird ueber
/api/build/<id> abgefragt (Log-Tail). Ergebnisse landen unter outputs/<id>/.
"""

from __future__ import annotations

import json
import os
import io
import time
import uuid
import shutil
import zipfile
import threading
import traceback

from flask import Flask, jsonify, request, send_file, render_template

import estimator
import generator

BASE = os.path.dirname(os.path.abspath(__file__))
OUTPUTS = os.path.join(BASE, "outputs")

app = Flask(__name__)
app.config["JSON_SORT_KEYS"] = False

JOBS = {}
JOBS_LOCK = threading.Lock()


# --------------------------------------------------------------------------
# Hilfen
# --------------------------------------------------------------------------

def spec_from_request() -> dict:
    data = request.get_json(silent=True) or {}
    spec = dict(estimator.catalog()["defaults"])
    spec.update({k: v for k, v in data.items() if v is not None})
    # Module immer als Liste von Strings
    mods = spec.get("modules") or []
    if isinstance(mods, str):
        mods = [mods]
    spec["modules"] = [str(m) for m in mods]
    return spec


def job_dir(job_id: str) -> str:
    return os.path.join(OUTPUTS, job_id)


def tail(path: str, lines: int = 200) -> str:
    if not os.path.isfile(path):
        return ""
    with open(path, "r", errors="replace") as fh:
        data = fh.readlines()
    return "".join(data[-lines:])


def read_json(path: str, default=None):
    try:
        with open(path) as fh:
            return json.load(fh)
    except Exception:
        return default


# --------------------------------------------------------------------------
# Seiten
# --------------------------------------------------------------------------

@app.route("/")
def index():
    return render_template("index.html")


# --------------------------------------------------------------------------
# API: Katalog
# --------------------------------------------------------------------------

@app.route("/api/catalog")
def api_catalog():
    cat = estimator.catalog()
    cat["chipyard"] = generator.chipyard_info(generator.CHIPYARD_ROOT)
    cat["config_class"] = generator.CONFIG_CLASS
    cat["config_path"] = os.path.join(generator.CHIPYARD_ROOT,
                                      generator.CONFIG_REL)
    return jsonify(cat)


# --------------------------------------------------------------------------
# API: Schaetzung
# --------------------------------------------------------------------------

@app.route("/api/estimate", methods=["POST"])
def api_estimate():
    spec = spec_from_request()
    try:
        result = estimator.estimate(spec)
    except ValueError as exc:
        return jsonify(ok=False, error=str(exc)), 400
    except Exception as exc:                       # pragma: no cover
        return jsonify(ok=False, error=str(exc),
                       trace=traceback.format_exc()), 500
    result["config_preview"] = generator.render_config(spec)["source"]
    return jsonify(result)


# --------------------------------------------------------------------------
# API: Config
# --------------------------------------------------------------------------

@app.route("/api/config", methods=["POST"])
def api_config():
    spec = spec_from_request()
    try:
        rendered = generator.render_config(spec)
    except Exception as exc:                       # pragma: no cover
        return jsonify(ok=False, error=str(exc),
                       trace=traceback.format_exc()), 500

    written = None
    if request.args.get("write") in ("1", "true", "yes"):
        try:
            written = generator.write_config(generator.CHIPYARD_ROOT,
                                             rendered["source"])
        except OSError as exc:
            return jsonify(ok=False, error="Schreiben fehlgeschlagen: %s" % exc,
                           source=rendered["source"]), 500

    return jsonify(ok=True, source=rendered["source"],
                   fragments=rendered["fragments"],
                   warnings=rendered["warnings"],
                   config_class=rendered["config_class"],
                   written=written)


# --------------------------------------------------------------------------
# API: Build (Hintergrund)
# --------------------------------------------------------------------------

def _run_job(job_id: str, spec: dict, root: str, jobs: int):
    d = job_dir(job_id)
    log_path = os.path.join(d, "build.log")
    state = JOBS[job_id]
    try:
        rendered = generator.render_config(spec)
        with open(os.path.join(d, "config.scala"), "w") as fh:
            fh.write(rendered["source"])
        with open(os.path.join(d, "spec.json"), "w") as fh:
            json.dump(spec, fh, indent=2)

        generator.write_config(root, rendered["source"])

        state["phase"] = "build"
        rc = generator.run_build(root, log_path, jobs=jobs)
        state["returncode"] = rc

        if rc == 0:
            state["phase"] = "collect"
            dest = os.path.join(d, "verilog")
            info = generator.collect_verilog(root, dest)
            state["verilog"] = info
            state["phase"] = "done" if info["found"] else "empty"
            state["status"] = "done" if info["found"] else "failed"
            if not info["found"]:
                state["error"] = ("Build war erfolgreich, aber im "
                                  "generated-src-Verzeichnis wurden keine "
                                  "Verilog-Dateien gefunden (%s)."
                                  % info.get("source_dir"))
        else:
            state["phase"] = "done"
            state["status"] = "failed"
            state["error"] = "make verilog endete mit Code %d" % rc
    except Exception as exc:                       # pragma: no cover
        state["status"] = "failed"
        state["error"] = str(exc)
        state["trace"] = traceback.format_exc()
        with open(log_path, "a") as fh:
            fh.write("\n::EXCEPTION::\n" + traceback.format_exc())
    finally:
        state["finished"] = time.time()
        # Zustand sofort persistieren, damit die Historie auch dann stimmt,
        # wenn die Oberflaeche nicht (mehr) pollt.
        try:
            with open(os.path.join(d, "state.json"), "w") as fh:
                json.dump({k: v for k, v in state.items()}, fh, indent=2)
        except OSError:
            pass


@app.route("/api/build", methods=["POST"])
def api_build():
    spec = spec_from_request()
    data = request.get_json(silent=True) or {}
    root = data.get("chipyard_root") or generator.CHIPYARD_ROOT
    jobs = int(data.get("jobs") or 4)
    jobs = max(1, min(jobs, 32))

    if not os.path.isdir(root):
        return jsonify(ok=False, error="Chipyard-Verzeichnis nicht gefunden: %s"
                       % root), 400
    if not generator.conda_available():
        return jsonify(ok=False, error=(
            "Conda-Umgebung nicht gefunden (%s / %s). Ohne sie kann kein "
            "Verilog-Build gestartet werden." %
            (generator.CONDA_SH, generator.CHIPYARD_ENV))), 400

    job_id = time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
    d = job_dir(job_id)
    os.makedirs(d, exist_ok=True)

    with JOBS_LOCK:
        JOBS[job_id] = dict(id=job_id, status="running", phase="prepare",
                            started=time.time(), finished=None, error=None,
                            returncode=None, dir=d, root=root, jobs=jobs,
                            spec=spec, verilog=None)

    th = threading.Thread(target=_run_job, args=(job_id, spec, root, jobs),
                          daemon=True)
    th.start()
    return jsonify(ok=True, job=JOBS[job_id])


@app.route("/api/build/<job_id>")
def api_build_status(job_id):
    state = JOBS.get(job_id)
    if state is None:
        state = read_json(os.path.join(job_dir(job_id), "state.json"))
        if state is None:
            return jsonify(ok=False, error="Unbekannter Job %s" % job_id), 404
    out = dict(state)
    out["log_tail"] = tail(os.path.join(job_dir(job_id), "build.log"), 80)
    out["elapsed_s"] = (state.get("finished") or time.time()) - \
        state.get("started", time.time())
    if state.get("status") in ("done", "failed") and \
            os.path.isdir(job_dir(job_id)):
        with open(os.path.join(job_dir(job_id), "state.json"), "w") as fh:
            json.dump({k: v for k, v in state.items()}, fh, indent=2)
    return jsonify(ok=True, job=out)


def _load_job(job_id: str):
    state = JOBS.get(job_id)
    if state:
        return state
    return read_json(os.path.join(job_dir(job_id), "state.json"))


def _verilog_dir(job_id: str) -> str:
    return os.path.join(job_dir(job_id), "verilog")


def _iter_files(base: str):
    """Alle Dateien unter base, mit relativen Pfaden (Schraegstriche)."""
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames.sort()
        for fname in sorted(filenames):
            p = os.path.join(dirpath, fname)
            rel = os.path.relpath(p, base).replace(os.sep, "/")
            yield rel, p


@app.route("/api/build/<job_id>/files")
def api_build_files(job_id):
    state = _load_job(job_id)
    if state is None:
        return jsonify(ok=False, error="Unbekannter Job"), 404
    d = _verilog_dir(job_id)
    files = []
    if os.path.isdir(d):
        files = [dict(name=rel, size=os.path.getsize(p))
                 for rel, p in _iter_files(d)]
    verilog = state.get("verilog") or {}
    return jsonify(ok=True, files=files, dir=d,
                   source_dir=verilog.get("source_dir"),
                   rtl_files=verilog.get("rtl_files"),
                   total_bytes=verilog.get("total_bytes"))


@app.route("/api/build/<job_id>/file/<path:name>")
def api_build_file(job_id, name):
    d = _verilog_dir(job_id)
    root = os.path.realpath(d)
    target = os.path.realpath(os.path.join(root, name))
    # Kein Ausbruch aus dem Job-Verzeichnis
    if target != root and not target.startswith(root + os.sep):
        return jsonify(ok=False, error="Ungueltiger Pfad"), 400
    if not os.path.isfile(target):
        return jsonify(ok=False, error="Datei nicht gefunden"), 404
    return send_file(target, as_attachment=False,
                     download_name=os.path.basename(target))


@app.route("/api/build/<job_id>/zip")
def api_build_zip(job_id):
    d = _verilog_dir(job_id)
    if not os.path.isdir(d):
        return jsonify(ok=False, error="Keine Verilog-Dateien vorhanden"), 404
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for rel, path in _iter_files(d):
            zf.write(path, rel)
        cfg = os.path.join(job_dir(job_id), "config.scala")
        if os.path.isfile(cfg):
            zf.write(cfg, "config.scala")
        spec = os.path.join(job_dir(job_id), "spec.json")
        if os.path.isfile(spec):
            zf.write(spec, "spec.json")
    buf.seek(0)
    return send_file(buf, mimetype="application/zip", as_attachment=True,
                     download_name="chipyard-verilog-%s.zip" % job_id)


@app.route("/api/jobs")
def api_jobs():
    items = []
    seen = set()
    if os.path.isdir(OUTPUTS):
        for name in sorted(os.listdir(OUTPUTS), reverse=True):
            d = os.path.join(OUTPUTS, name)
            if not os.path.isdir(d):
                continue
            seen.add(name)
            st = JOBS.get(name) or read_json(os.path.join(d, "state.json")) or {}
            spec = st.get("spec") or read_json(os.path.join(d, "spec.json")) or {}
            items.append(dict(
                id=name,
                status=st.get("status", "unbekannt"),
                started=st.get("started"),
                finished=st.get("finished"),
                cores=spec.get("cores"),
                core_type=spec.get("core_type"),
                pdk=spec.get("pdk"),
                error=st.get("error"),
                has_verilog=os.path.isdir(os.path.join(d, "verilog")),
            ))
    return jsonify(ok=True, jobs=items)


# --------------------------------------------------------------------------
# Start
# --------------------------------------------------------------------------

def main():
    os.makedirs(OUTPUTS, exist_ok=True)
    port = int(os.environ.get("PORT", 5000))
    debug = os.environ.get("FLASK_DEBUG", "0") in ("1", "true", "yes")
    print("Chipyard GUI laeuft auf http://127.0.0.1:%d" % port)
    print("Chipyard-Root: %s" % generator.CHIPYARD_ROOT)
    print("Config-Ziel : %s" % os.path.join(generator.CHIPYARD_ROOT,
                                            generator.CONFIG_REL))
    app.run(host="127.0.0.1", port=port, debug=debug, threaded=True)


if __name__ == "__main__":
    main()
