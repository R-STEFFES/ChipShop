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

    def test_unavailable_module_warns_and_is_skipped(self):
        r = generator.render_config(spec(modules=["uart", "nvdla"]))
        self.assertTrue(any("nvdla" in w.lower() or "NVDLA" in w
                            for w in r["warnings"]))
        self.assertNotIn("nvdla", body_of(r["source"]).lower())

    def test_rocc_clamped_to_cores(self):
        r = generator.render_config(spec(cores=2, rocc_count=5))
        self.assertIn("WithMultiRoCCFromBuildRoCC(0, 1)", r["source"])
        self.assertTrue(any("begrenzt" in w for w in r["warnings"]))

    def test_l2_size_respected(self):
        src = generator.render_config(spec(l2_kib=1024))["source"]
        self.assertIn("capacityKB = 1024", src)


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
        self.assertEqual([], missing, "Fragmente ohne Scala-Klasse: %s" % missing)


if __name__ == "__main__":
    unittest.main(verbosity=2)
