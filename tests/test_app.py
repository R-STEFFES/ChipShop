"""Tests for Flask-side build diagnostics and catalog capabilities."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as app_module  # noqa: E402


class BuildDiagnosticTest(unittest.TestCase):

    def test_memory_failure_explains_cause_and_remediation(self):
        result = app_module.diagnose_build_failure(
            "[error] java.lang.OutOfMemoryError: Java heap space\nmake: *** [verilog] Error 1\n",
            "build",
        )
        self.assertIn("Speicher", result["reason"])
        self.assertIn("RAM", result["suggestion"])

    def test_killed_process_suggests_memory_check(self):
        result = app_module.diagnose_build_failure("Killed\n", "build")
        self.assertIn("Speicher", result["reason"])
        self.assertIn("-j", result["suggestion"])

    def test_mixed_core_estimate_api_returns_each_cluster(self):
        response = app_module.app.test_client().post("/api/estimate", json={
            "core_mix": {"rocket": 2, "boom_small": 6, "boom_large": 2},
            "cores": 10,
        })
        self.assertEqual(200, response.status_code)
        result = response.get_json()
        self.assertEqual(10, result["spec"]["cores"])
        self.assertEqual(3, sum(1 for block in result["floorplan"]["blocks"]
                                if block["kind"] == "cores"))

    def test_ui_exposes_project_settings_and_preview(self):
        response = app_module.app.test_client().get("/")
        self.assertEqual(200, response.status_code)
        page = response.get_data(as_text=True)
        for element in ("btn-save-project", "btn-load-project", "btn-settings",
                        "floorplan", "resource-warning", "build-diagnostic"):
            self.assertIn(element, page)

    def test_ui_exposes_gpu_l3_npu_and_simulation(self):
        page = app_module.app.test_client().get("/").get_data(as_text=True)
        for element in ("gpu-dialog", "btn-gpu-pick", "l3-enabled", "l3-ways",
                        "l3-banks", "sim-partners", "dram-model", "dram-size",
                        "workload", "btn-sim-run", "sim-panel", "help-dialog",
                        "col-body", "plain-summary", "npu-enabled", "npu",
                        "linux-target", "linux-status", "recommendations"):
            self.assertIn(element, page)

    def test_ui_ships_the_sti_prefabs(self):
        client = app_module.app.test_client()
        response = client.get("/static/js/app.js")
        js = response.get_data(as_text=True)
        response.close()
        for name in ("STI-Fento", "STI-Nano", "STI-Mikro", "STI-Mill",
                     "STI-Meter"):
            self.assertIn(name, js)

    def test_catalog_ships_cores_npus_and_linux_info(self):
        data = app_module.app.test_client().get("/api/catalog").get_json()
        cores = {c["id"]: c for c in data["core_types"]}
        for core_id in ("ibex", "cva6", "vexii", "rocket", "boom_large"):
            self.assertIn(core_id, cores)
            self.assertIn("bits", cores[core_id])
            self.assertIn("linux", cores[core_id])
        self.assertFalse(cores["ibex"]["linux"])
        self.assertEqual(32, cores["ibex"]["bits"])
        npus = {n["id"] for n in data["npus"]}
        self.assertTrue({"gemmini_16", "gemmini_32", "nvdla_small"} <= npus)
        self.assertEqual(256, data["linux_min_dram_mib"])

    def test_npu_and_linux_survive_the_api(self):
        response = app_module.app.test_client().post("/api/estimate", json={
            "core_mix": {"cva6": 2}, "npu_enabled": True,
            "npu_id": "nvdla_small", "linux_target": True,
            "dram_mib": 512, "l3_kib": 1024,
            "modules": ["uart", "gpio", "dmi", "blockdev", "spiflash"]})
        self.assertEqual(200, response.status_code)
        result = response.get_json()
        self.assertEqual("nvdla_small", result["npu"]["id"])
        self.assertTrue(result["linux"]["ready"], result["linux"]["issues"])
        self.assertIn("WithNVDLA", result["config_preview"])

    def test_legacy_gemmini_module_becomes_the_npu(self):
        response = app_module.app.test_client().post("/api/config", json={
            "modules": ["uart", "gpio", "dmi", "gemmini"]})
        self.assertEqual(200, response.status_code)
        body = response.get_json()["source"]
        self.assertIn("gemmini.DefaultGemminiConfig", body)
        self.assertNotIn("Unbekanntes Modul", body)

    def test_footer_says_germany(self):
        page = app_module.app.test_client().get("/").get_data(as_text=True)
        self.assertIn("Germany", page)
        self.assertNotIn("Raphael Steffes &middot;", page)

    def test_every_theme_has_a_css_block(self):
        import re
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(base, "static/js/app.js"), encoding="utf-8") as fh:
            js = fh.read()
        with open(os.path.join(base, "static/css/style.css"),
                  encoding="utf-8") as fh:
            css = fh.read()
        m = re.search(r"const THEMES = \[(.*?)\];", js, re.S)
        if m is None:
            self.fail("THEMES-Liste fehlt in app.js")
        ids = re.findall(r"id: '([a-z0-9_-]+)'", m.group(1))
        self.assertGreaterEqual(len(ids), 8)
        self.assertIn("dark", ids)
        # "dark" ist der :root-Standard und braucht keinen eigenen Block.
        for theme in [t for t in ids if t != "dark"]:
            self.assertTrue('html[data-theme="%s"]' % theme in css,
                            "Theme %s fehlt in style.css" % theme)

    def test_catalog_ships_gpus_partners_and_l3_choices(self):
        data = app_module.app.test_client().get("/api/catalog").get_json()
        self.assertTrue(data["gpus"])
        self.assertTrue(any(p["id"] == "blkdev" for p in data["sim_partners"]))
        self.assertIn(1024, data["l3_choices"])
        self.assertIn(8, data["l3_ways_choices"])
        self.assertIn(4, data["l3_bank_choices"])
        self.assertIn(256, data["dram_size_choices"])
        self.assertAlmostEqual(28.0, data["area_ref_nm"])
        self.assertIn("simulator_built", data["chipyard"])

    def test_workload_endpoint_lists_programs(self):
        data = app_module.app.test_client().get("/api/workloads").get_json()
        self.assertTrue(data["ok"])
        self.assertIsInstance(data["workloads"], list)
        if data["workloads"]:
            first = data["workloads"][0]
            self.assertTrue(os.path.isfile(first["path"]))
            self.assertIn("group", first)

    def test_simulate_requires_workload_for_a_run(self):
        client = app_module.app.test_client()
        response = client.post("/api/simulate", json={"run": True})
        self.assertEqual(400, response.status_code)
        self.assertIn("Programm", response.get_json()["error"])

    def test_simulate_rejects_missing_program(self):
        client = app_module.app.test_client()
        response = client.post("/api/simulate", json={
            "run": True, "workload": "/gibt/es/nicht.riscv"})
        self.assertEqual(400, response.status_code)

    def test_estimate_accepts_l3_gpu_and_sim_partners(self):
        response = app_module.app.test_client().post("/api/estimate", json={
            "l3_enabled": True, "l3_kib": 2048, "l3_ways": 8, "l3_banks": 2,
            "gpu_enabled": True, "gpu_id": "mali_g78",
            "sim_partners": ["dram", "uart"], "dram_model": "aximem",
            "dram_mib": 512,
        })
        self.assertEqual(200, response.status_code)
        result = response.get_json()
        self.assertEqual("mali_g78", result["gpu"]["id"])
        self.assertEqual(512, result["spec"]["dram_mib"])
        self.assertEqual("aximem", result["spec"]["dram_model"])
        self.assertIn("WithInclusiveCache", result["config_preview"])
        self.assertIn("WithSimAXIMem", result["config_preview"])

    def test_scala_failure_reports_source_location(self):
        result = app_module.diagnose_build_failure(
            "[error] /chipyard/generators/demo/src/main/scala/Foo.scala:42: not found: value bar\n",
            "build",
        )
        self.assertEqual("Foo.scala:42", result["location"])
        self.assertIn("Foo.scala", result["reason"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
