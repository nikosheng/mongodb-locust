import csv
import importlib.util
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from bench.config import Load
from bench.throughput import summarize_rps


class ThroughputTest(unittest.TestCase):
    def test_excludes_startup_and_keeps_zero_seconds(self):
        counts = {second: 1 for second in range(100, 110)}
        counts.update({110: 20, 112: 30, 113: 10})
        summary, samples = summarize_rps(counts, 100, 114)
        self.assertEqual(samples, [(110, 20), (111, 0), (112, 30), (113, 10)])
        self.assertEqual(summary["avg_rps"], 15)
        self.assertEqual(summary["median_rps"], 15)
        self.assertEqual(summary["max_rps"], 30)

    def test_median_resists_low_outlier(self):
        summary, _ = summarize_rps({110: 1, 111: 100, 112: 100}, 100, 113)
        self.assertEqual(summary["avg_rps"], 67)
        self.assertEqual(summary["median_rps"], 100)

    def test_partial_seconds_are_excluded(self):
        summary, samples = summarize_rps({110: 999, 111: 5, 112: 7, 113: 999}, 100.2, 113.8)
        self.assertEqual(samples, [(111, 5), (112, 7)])
        self.assertEqual(summary["rps_samples"], 2)
        self.assertEqual(summary["max_rps"], 7)

    def test_short_run_has_no_throughput_not_zero(self):
        summary, samples = summarize_rps({}, 100, 110)
        self.assertEqual(samples, [])
        self.assertIsNone(summary["median_rps"])
        self.assertIsNone(summary["avg_rps"])
        self.assertIsNone(summary["max_rps"])

    def test_all_zero_samples_are_real_zero_throughput(self):
        summary, _ = summarize_rps({}, 100, 113)
        self.assertEqual(summary["median_rps"], 0)
        self.assertEqual(summary["rps_samples"], 3)

    def test_exclusion_is_configurable_and_nonnegative(self):
        self.assertEqual(Load(users=1, duration_seconds=20).rps_exclude_seconds, 10)
        summary, samples = summarize_rps({100: 5}, 100, 101, exclude_seconds=0)
        self.assertEqual(samples, [(100, 5)])
        self.assertEqual(summary["median_rps"], 5)
        with self.assertRaises(ValueError):
            Load(users=1, duration_seconds=20, rps_exclude_seconds=-1)


class SnapshotTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        cfg = SimpleNamespace(load=Load(users=1, spawn_rate=1, duration_seconds=13))

        class Shape:
            def __init__(self):
                self.runner = None

        class Hook:
            def add_listener(self, listener):
                return listener

        locust = SimpleNamespace(LoadTestShape=Shape, User=object, constant=lambda x: x,
                                 task=lambda f: f, events=SimpleNamespace(quitting=Hook()))
        path = Path(__file__).resolve().parents[1] / "bench/loadtest/locustfile.py"
        spec = importlib.util.spec_from_file_location("test_locustfile", path)
        module = importlib.util.module_from_spec(spec)
        with patch.dict("sys.modules", {"locust": locust}), \
             patch.dict(os.environ, BENCH_CONFIG="unused", BENCH_RUN_DIR=str(self.base)), \
             patch("bench.config.load_config", return_value=cfg), \
             patch("bench.scenarios.resolve_mix", return_value=[("query", 1)]), \
             patch("bench.scenarios.target_collection", return_value="customers"):
            spec.loader.exec_module(module)
        self.module = module
        self.shape = module.StepShape()
        self.entry = SimpleNamespace(
            num_requests=30, num_failures=0, num_reqs_per_sec={1031: 10},
            avg_response_time=2, max_response_time=3,
            get_response_time_percentile=lambda p: 2,
        )
        self.resets = 0

        def reset():
            self.resets += 1

        self.shape.runner = SimpleNamespace(
            user_count=1, stats=SimpleNamespace(total=self.entry, entries={("query", "AGG"): self.entry},
                                                reset_all=reset),
        )

    def tick(self, runtime, wall):
        self.shape.get_run_time = lambda: runtime
        with patch.object(self.module.time, "time", return_value=wall):
            return self.shape.tick()

    def test_final_snapshot_waits_for_worker_counts_and_preserves_boundaries(self):
        self.tick(0, 1000)
        self.tick(21, 1021)
        self.assertEqual(self.resets, 1)
        self.assertIsNone(self.tick(34, 1034))
        self.assertFalse((self.base / "steps.csv").exists())
        # Simulate the final worker report, arriving after the shape ends.
        self.entry.num_reqs_per_sec.update({1032: 20, 1033: 30, 1034: 999})
        self.module.save_final_snapshot(SimpleNamespace(shape_class=self.shape))
        with (self.base / "steps.csv").open() as f:
            rows = list(csv.DictReader(f))
        self.assertEqual(len(rows), 2)
        self.assertEqual(float(rows[0]["duration_s"]), 13)
        self.assertEqual(float(rows[0]["median_rps"]), 20)
        self.assertEqual(float(rows[0]["max_rps"]), 30)
        self.assertEqual(int(rows[0]["rps_samples"]), 3)
        with (self.base / "rps.csv").open() as f:
            samples = list(csv.DictReader(f))
        self.assertEqual([int(r["timestamp"]) for r in samples[:3]], [1031, 1032, 1033])
        self.shape.finish()
        self.assertEqual(self.resets, 2)  # finishing twice does not duplicate output


if __name__ == "__main__":
    unittest.main()
