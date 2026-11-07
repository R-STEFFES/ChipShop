"""Tests fuer die Schaetz-Engine und den Config-Generator.

Laeuft ohne Zusatzpakete:  venv/bin/python -m unittest discover -s tests -v

Die wichtigsten Faelle sind Regressionstests:
  * Module, die schon im Basis-Config stecken (in_base), duerfen NICHT noch
    einmal als Fragment auftauchen -- das erzeugt ein zweites Geraet auf
    derselben Adresse ("Ports cannot overlap") und bricht den Build.
  * Jedes Fragment im Katalog muss als Scala-Klasse im Chipyard-Checkout
    existieren, sonst laeuft `make verilog` in einen Compile-Fehler.
"""

import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import estimator  # noqa: E402
import generator  # noqa: E402


def spec(**over):
    s = dict(estimator.catalog()["defaults"])
    s.update(over)
    return s


def body_of(source):
    """Nur den Config-Rumpf, ohne Kommentar-Header."""
    return source.split("extends Config(", 1)[1]


class AreaTest(unittest.TestCase):

    def test_node_scaling_monotonic(self):
        self.assertGreater(estimator.node_scale(130), estimator.node_scale(28))
        self.assertGreater(estimator.node_scale(28), estimator.node_scale(7))

    def test_more_cores_means_bigger_die(self):
        small = estimator.estimate(spec(cores=1))
        big = estimator.estimate(spec(cores=16))
        self.assertGreater(big["area"]["die_mm2"], small["area"]["die_mm2"])
        self.assertGreater(big["perf"]["dmips"], small["perf"]["dmips"])

    def test_finer_node_shrinks_area(self):
        big = estimator.estimate(spec(pdk="sky130"))
        fine = estimator.estimate(spec(pdk="generic7"))
        self.assertGreater(big["area"]["die_mm2"], fine["area"]["die_mm2"])

    def test_wafer_and_yield_sane(self):
        r = estimator.estimate(spec(cores=8))
        w = r["wafer"]
        self.assertGreater(w["dies_per_wafer"], 0)
        self.assertTrue(0.0 < w["yield_pct"] <= 1.0)
        self.assertLessEqual(w["good_dies"], w["dies_per_wafer"])
        # Groessere Dies -> weniger Dies pro Wafer
        small = estimator.estimate(spec(cores=1))["wafer"]
        self.assertGreater(small["dies_per_wafer"], w["dies_per_wafer"])

    def test_floorplan_covers_all_blocks(self):
        r = estimator.estimate(spec(cores=4, modules=["uart", "gpio", "dmi"]))
        fp = r["floorplan"]
        self.assertTrue(fp["blocks"])
        for b in fp["blocks"]:
            self.assertGreater(b["w"], 0)
            self.assertGreater(b["h"], 0)
            self.assertLessEqual(b["x"] + b["w"], fp["content_w"] + 1e-6)
            self.assertLessEqual(b["y"] + b["h"], fp["content_h"] + 1e-6)

    def test_floorplan_searches_for_more_compact_packing_width(self):
        blocks = [dict(area=area, aspect=aspect) for area, aspect in (
            (18.96, 0.62), (1.06, 4.39), (1.97, 1.46),
            (18.2, 0.27), (1.81, 0.69), (4.89, 1.27),
        )]
        _placed, width, height = estimator._shelf_pack(blocks)
        self.assertLessEqual(width * height, 62.0)

    def test_estimated_logic_never_exceeds_floorplan_capacity(self):
        for core_type in estimator.CORE_TYPES:
            result = estimator.estimate(spec(core_mix={core_type: 64}))
            with self.subTest(core_type=core_type):
                self.assertLessEqual(result["area"]["utilization"], 1.0)

    def test_mixed_core_counts_are_estimated_individually(self):
        result = estimator.estimate(spec(core_mix={
            "rocket": 2, "boom_small": 6, "boom_large": 2,
        }))

        self.assertEqual(10, result["spec"]["cores"])
        self.assertEqual({"rocket": 2, "boom_small": 6, "boom_large": 2},
                         result["spec"]["core_mix"])
        self.assertAlmostEqual(
            2 * estimator.CORE_TYPES["rocket"]["area28"] +
            6 * estimator.CORE_TYPES["boom_small"]["area28"] +
            2 * estimator.CORE_TYPES["boom_large"]["area28"],
            result["area"]["core_total_mm2"] / estimator.node_scale(130),
        )
        self.assertEqual(3, sum(1 for b in result["floorplan"]["blocks"]
                                if b["kind"] == "cores"))

    def test_mixed_core_counts_reject_fractional_quantities(self):
        with self.assertRaisesRegex(ValueError, "Kernanzahl"):
            estimator.estimate(spec(core_mix={"rocket": 1.5}))

    def test_mixed_core_total_is_limited_to_64(self):
        with self.assertRaisesRegex(ValueError, "64"):
            estimator.estimate(spec(core_mix={"rocket": 65}))

    def test_rejects_bad_input(self):
        with self.assertRaises(ValueError):
            estimator.estimate(spec(pdk="kein-pdk"))
        with self.assertRaises(ValueError):
            estimator.estimate(spec(core_type="kein-kern"))
        with self.assertRaises(ValueError):
            estimator.estimate(spec(cores=0))


class ConfigTest(unittest.TestCase):

    def test_uart_not_duplicated(self):
        """UART steckt im AbstractConfig -- kein zweites WithUART."""
        src = generator.render_config(spec(modules=["uart", "gpio", "dmi"]))["source"]
        self.assertNotIn("chipyard.config.WithUART +", body_of(src))

    def test_deselecting_uart_removes_it(self):
        src = generator.render_config(spec(modules=["gpio", "dmi"]))["source"]
        self.assertIn("chipyard.config.WithNoUART", body_of(src))

    def test_base_last(self):
        src = generator.render_config(spec())["source"]
        body = body_of(src)
        self.assertTrue(body.rstrip().rstrip(")").rstrip().endswith(
            "new chipyard.config.AbstractConfig"), body)

    def test_selected_modules_appear(self):
        src = generator.render_config(
            spec(modules=["uart", "gpio", "spi", "i2c", "spiflash", "dmi",
                          "blockdev"]))["source"]
        for frag in ("WithGPIO", "WithSPI", "WithI2C", "WithSPIFlash",
                     "WithDMIDTM", "iceblk.WithBlockDevice"):
            self.assertIn(frag, src)

    def test_mixed_core_types_render_separate_fragments(self):
        source = generator.render_config(spec(
            core_mix={"rocket": 2, "boom_small": 6, "boom_large": 2}
        ))["source"]

        self.assertIn("new freechips.rocketchip.rocket.WithNHugeCores(2)", source)
        self.assertIn("new boom.v4.common.WithNSmallBooms(6)", source)
        self.assertIn("new boom.v4.common.WithNLargeBooms(2)", source)

    def test_unavailable_module_warns_and_is_skipped(self):
        r = generator.render_config(spec(modules=["uart", "ara"]))
        self.assertTrue(any("ara" in w.lower() or "Ara" in w
                            for w in r["warnings"]))
        self.assertNotIn("ara", body_of(r["source"]).lower())

    def test_rocc_clamped_to_cores(self):
        r = generator.render_config(spec(cores=2, rocc_count=5))
        self.assertIn("WithMultiRoCCFromBuildRoCC(0, 1)", r["source"])
        self.assertTrue(any("begrenzt" in w for w in r["warnings"]))

    def test_l3_size_respected(self):
        src = generator.render_config(spec(l3_kib=1024, l3_ways=4))["source"]
        self.assertIn("WithInclusiveCache(nWays = 4, capacityKB = 1024)", src)

    def test_l3_can_be_switched_off(self):
        src = generator.render_config(spec(l3_enabled=False))["source"]
        self.assertNotIn("WithInclusiveCache", src)
        self.assertIn("chipyard.config.WithBroadcastManager", src)

    def test_l3_banks_are_rendered_after_the_cache(self):
        src = generator.render_config(spec(l3_banks=4))["source"]
        self.assertIn("WithNBanks(4)", src)
        # WithInclusiveCache liest nBanks aus dem rechten (spaeteren) Fragment
        self.assertLess(src.index("WithInclusiveCache"),
                        src.index("WithNBanks(4)"))

    def test_legacy_l2_kib_is_still_accepted_by_the_api(self):
        import app as app_module
        client = app_module.app.test_client()
        response = client.post("/api/config", json={"l2_kib": 2048})
        self.assertEqual(200, response.status_code)
        self.assertIn("capacityKB = 2048", response.get_json()["source"])


class GpuAndSimulationTest(unittest.TestCase):
    """GPU-Katalog, Simulationspartner und DRAM-Abbildung."""

    def test_gpu_adds_area_and_floorplan_block(self):
        without = estimator.estimate(spec())
        with_gpu = estimator.estimate(spec(gpu_enabled=True, gpu_id="mali_g78"))
        self.assertGreater(with_gpu["area"]["gpu_mm2"], 0)
        self.assertGreater(with_gpu["area"]["die_mm2"], without["area"]["die_mm2"])
        self.assertEqual(1, sum(1 for b in with_gpu["floorplan"]["blocks"]
                                if b["kind"] == "gpu"))
        self.assertGreater(with_gpu["perf"]["gflops_gpu"], 0)

    def test_unknown_gpu_is_ignored(self):
        result = estimator.estimate(spec(gpu_enabled=True, gpu_id="gibtsnicht"))
        self.assertIsNone(result["gpu"])
        self.assertEqual(0, result["area"]["gpu_mm2"])

    def test_gpu_has_no_fragment_and_warns(self):
        rendered = generator.render_config(spec(gpu_enabled=True,
                                                gpu_id="mali_g78"))
        self.assertTrue(any("Mali" in w for w in rendered["warnings"]))
        # Kein GPU-Fragment im Config-Rumpf (der Kommentarkopf darf sie nennen)
        self.assertFalse(any("mali" in f.lower() for f in rendered["fragments"]))

    def test_l3_off_uses_less_area(self):
        on = estimator.estimate(spec(l3_enabled=True, l3_kib=4096))
        off = estimator.estimate(spec(l3_enabled=False))
        self.assertLess(off["area"]["l3_mm2"], on["area"]["l3_mm2"])

    def test_l3_banks_grow_area(self):
        one = estimator.estimate(spec(l3_banks=1))
        four = estimator.estimate(spec(l3_banks=4))
        self.assertGreater(four["area"]["l3_mm2"], one["area"]["l3_mm2"])

    def test_dram_size_and_model_reach_the_config(self):
        src = generator.render_config(spec(
            dram_mib=1024, dram_model="aximem", modules=["uart"]))["source"]
        self.assertIn("WithExtMemSize(BigInt(1073741824))", src)
        self.assertIn("chipyard.harness.WithSimAXIMem", src)
        self.assertNotIn("WithBlackBoxSimMem(", src)

    def test_simdram_latency_override(self):
        src = generator.render_config(spec(dram_latency=7))["source"]
        self.assertIn("WithBlackBoxSimMem(additionalLatency = 7)", src)

    def test_block_device_partner_needs_soc_module(self):
        with_module = generator.render_config(spec(
            modules=["uart", "blockdev"], sim_partners=["dram", "blkdev"]))
        self.assertIn("chipyard.harness.WithSimBlockDevice", with_module["source"])
        without = generator.render_config(spec(
            modules=["uart"], sim_partners=["dram", "blkdev"]))
        self.assertNotIn("WithSimBlockDevice", without["source"])
        self.assertTrue(any("Block-Device" in w for w in without["warnings"]))

    def test_ram_estimate_follows_dram_size(self):
        small = estimator.estimate(spec(dram_mib=64))
        big = estimator.estimate(spec(dram_mib=4096))
        self.assertGreater(big["sim"]["host_ram_gb"],
                           small["sim"]["host_ram_gb"] + 3.0)

    def test_aximem_is_faster_than_simdram(self):
        slow = estimator.estimate(spec(dram_model="simdram"))
        fast = estimator.estimate(spec(dram_model="aximem"))
        self.assertGreater(fast["sim"]["verilator_throughput_mcyc_s"],
                           slow["sim"]["verilator_throughput_mcyc_s"])

    def test_sim_partner_fragments_exist_in_checkout(self):
        root = generator.CHIPYARD_ROOT
        if not os.path.isdir(root):
            self.skipTest("Chipyard-Checkout nicht vorhanden: %s" % root)
        blob = []
        for dirpath, dirnames, filenames in os.walk(
                os.path.join(root, "generators", "chipyard")):
            dirnames[:] = [d for d in dirnames if d not in ("target", ".git")]
            for fn in filenames:
                if fn.endswith(".scala"):
                    with open(os.path.join(dirpath, fn), errors="replace") as fh:
                        blob.append(fh.read())
        haystack = "\n".join(blob)
        missing = []
        for partner in estimator.SIM_PARTNERS:
            frag = partner.get("fragment")
            if not frag:
                continue
            cls = frag.split(".")[-1].split("(")[0]
            if not re.search(r"\bclass\s+%s\b" % re.escape(cls), haystack):
                missing.append("%s -> %s" % (partner["id"], frag))
        self.assertEqual([], missing)


class CoreAndNpuTest(unittest.TestCase):
    """Neue Kerntypen (Ibex/CVA6/VexiiRiscv), NPU-Groessen und Linux-Check."""

    def test_new_core_types_render_their_fragments(self):
        cases = {
            "ibex": "new ibex.WithNIbexCores(2)",
            "cva6": "new cva6.WithNCVA6Cores(2)",
            "vexii": "new vexiiriscv.WithNVexiiRiscvCores(2)",
        }
        for core, expected in cases.items():
            with self.subTest(core=core):
                src = generator.render_config(
                    spec(core_mix={core: 2}))["source"]
                self.assertIn(expected, src)

    def test_ibex_sets_32bit_cache_write_bytes(self):
        src = generator.render_config(spec(core_mix={"ibex": 1}))["source"]
        self.assertIn("WithInclusiveCacheWriteBytes(4)", src)

    def test_core_types_declare_bits_and_linux(self):
        for core_id, core in estimator.CORE_TYPES.items():
            with self.subTest(core=core_id):
                self.assertIn(core["bits"], (32, 64))
                self.assertIn("linux", core)
                self.assertEqual(core["linux"], core["bits"] == 64)

    def test_mixed_rv32_and_rv64_warns(self):
        r = generator.render_config(
            spec(core_mix={"ibex": 1, "rocket": 1}))
        self.assertTrue(any("RV32" in w and "RV64" in w
                            for w in r["warnings"]))

    def test_npu_sizes_render_their_fragments(self):
        cases = {
            "gemmini_16": "new gemmini.DefaultGemminiConfig",
            "gemmini_32": "gemmini.GemminiConfigs.largeChipConfig",
            "gemmini_chip": "gemmini.GemminiConfigs.chipConfig",
            "nvdla_small": 'nvidia.blocks.dla.WithNVDLA("small")',
            "nvdla_large": 'nvidia.blocks.dla.WithNVDLA("large", true)',
        }
        for npu_id, expected in cases.items():
            with self.subTest(npu=npu_id):
                r = generator.render_config(
                    spec(npu_enabled=True, npu_id=npu_id))
                self.assertIn(expected, r["source"])
                self.assertIn("WithSystemBusWidth(128)", r["source"])

    def test_npu_grows_area_and_macs(self):
        without = estimator.estimate(spec())
        small = estimator.estimate(spec(npu_enabled=True, npu_id="gemmini_16"))
        big = estimator.estimate(spec(npu_enabled=True, npu_id="gemmini_32"))
        self.assertEqual(0, without["perf"]["npu_macs"])
        self.assertGreater(small["area"]["npu_mm2"], 0)
        self.assertGreater(big["area"]["npu_mm2"], small["area"]["npu_mm2"])
        self.assertGreater(big["perf"]["npu_macs"], small["perf"]["npu_macs"])
        self.assertEqual(1, sum(1 for b in big["floorplan"]["blocks"]
                                if b["kind"] == "npu"))

    def test_linux_ready_needs_rv64_l3_dram_and_modules(self):
        ready = estimator.estimate(spec(
            core_mix={"rocket": 2}, l3_enabled=True, dram_mib=512,
            modules=["uart", "gpio", "dmi", "blockdev", "spiflash"]))
        self.assertTrue(ready["linux"]["ready"], ready["linux"]["issues"])

        rv32 = estimator.estimate(spec(
            core_mix={"ibex": 1}, modules=["uart", "blockdev", "spiflash"]))
        self.assertFalse(rv32["linux"]["ready"])
        self.assertTrue(any("RV32" in i for i in rv32["linux"]["issues"]))

        no_l3 = estimator.estimate(spec(
            l3_enabled=False, dram_mib=512,
            modules=["uart", "blockdev", "spiflash"]))
        self.assertFalse(no_l3["linux"]["ready"])

        small_dram = estimator.estimate(spec(
            dram_mib=64, modules=["uart", "blockdev", "spiflash"]))
        self.assertFalse(small_dram["linux"]["ready"])

    def test_recommendations_point_at_missing_pieces(self):
        est = estimator.estimate(spec(cores=4, core_mix={"rocket": 4},
                                      dram_mib=64))
        kinds = {r["kind"] for r in est["recommendations"]}
        self.assertIn("dram_mib", kinds)
        self.assertIn("module", kinds)
        self.assertIn("gpu", kinds)

    def test_linux_target_is_reported(self):
        est = estimator.estimate(spec(linux_target=True))
        self.assertTrue(est["linux"]["target"])
        self.assertIn("Linux-Ziel: ja", generator.render_config(
            spec(linux_target=True))["source"])


class FragmentExistenceTest(unittest.TestCase):
    """Jedes Katalog-Fragment muss im Chipyard-Checkout existieren."""

    def test_fragments_exist_in_checkout(self):
        root = generator.CHIPYARD_ROOT
        if not os.path.isdir(root):
            self.skipTest("Chipyard-Checkout nicht vorhanden: %s" % root)
        # Alle Scala-Quellen einmal einlesen
        blob = []
        for sub in ("generators", "tools"):
            for dirpath, dirnames, filenames in os.walk(os.path.join(root, sub)):
                dirnames[:] = [d for d in dirnames
                               if d not in ("target", ".git", "test")]
                for fn in filenames:
                    if fn.endswith(".scala"):
                        try:
                            with open(os.path.join(dirpath, fn),
                                      errors="replace") as fh:
                                blob.append(fh.read())
                        except OSError:
                            pass
        if not blob:
            self.skipTest("keine Scala-Quellen gefunden")
        haystack = "\n".join(blob)

        missing = []
        for mod in estimator.MODULES:
            frag = mod.get("fragment")
            if not frag:
                continue
            cls = frag.split("(")[0].split(".")[-1]
            if not re.search(r"\bclass\s+%s\b" % re.escape(cls), haystack):
                missing.append("%s -> %s" % (mod["id"], frag))
        for npu in estimator.NPUS:
            frag = str(npu["fragment"])
            cls = frag.split("(")[0].split(".")[-1]
            if not re.search(r"\bclass\s+%s\b" % re.escape(cls), haystack):
                missing.append("%s -> %s" % (npu["id"], frag))
        for core_id, core in estimator.CORE_TYPES.items():
            frag = str(core["fragment"])
            cls = frag.split("(")[0].split(".")[-1]
            if not re.search(r"\bclass\s+%s\b" % re.escape(cls), haystack):
                missing.append("%s -> %s" % (core_id, frag))
        self.assertEqual([], missing, "Fragmente ohne Scala-Klasse: %s" % missing)


if __name__ == "__main__":
    unittest.main(verbosity=2)
