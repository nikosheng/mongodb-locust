import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


spec = importlib.util.spec_from_file_location("bench_cli", Path(__file__).resolve().parents[1] / "bench.py")
bench_cli = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bench_cli)


class RunReportTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.cfg = SimpleNamespace(
            output=SimpleNamespace(dir=str(self.base)),
            scenario=SimpleNamespace(active="popular_tag"),
            thresholds=SimpleNamespace(p95_ms=None, max_error_rate=None),
        )

    def make_run(self, name):
        run = self.base / name
        run.mkdir()
        (run / "steps.csv").write_text(
            "name,stage,users,requests,failures,rps,avg_ms,p50_ms,p95_ms,p99_ms,max_ms\n"
            "Aggregated,steady,10,100,0,10,2,2,3,4,5\n"
        )
        (run / "meta.json").write_text(json.dumps({
            "server_version": "9", "pymongo": "4", "customers": 100,
        }))
        return run

    def invoke(self, command, runs):
        with patch.object(sys, "argv", ["bench.py", command, "--scenario", "popular_tag"]), \
             patch("bench.config.load_config", return_value=self.cfg) as load_config, \
             patch("bench.runner.run_scenario", return_value=runs) as run:
            bench_cli.main()
        self.assertEqual(load_config.call_args.args[1]["scenario.active"], "popular_tag")
        return run

    def test_run_builds_report_from_saved_results(self):
        self.make_run("20260101_000000_popular_tag_r1")
        current = self.make_run("20260102_000000_popular_tag_r1")
        run = self.invoke("run", [current])
        run.assert_called_once_with(self.cfg)
        report_dir = self.base / "report_popular_tag"
        self.assertIn("Runs: 2", (report_dir / "report.md").read_text())
        self.assertTrue((report_dir / "latency.png").is_file())
        self.assertTrue((report_dir / "summary.csv").is_file())

    def test_missing_current_results_do_not_report_old_runs(self):
        self.make_run("20260101_000000_popular_tag_r1")
        current = self.base / "20260102_000000_popular_tag_r1"
        current.mkdir()
        with self.assertRaisesRegex(SystemExit, "did not produce results"):
            self.invoke("run", [current])
        self.assertFalse((self.base / "report_popular_tag").exists())

    def test_all_reports_once_after_run(self):
        current = self.make_run("20260102_000000_popular_tag_r1")
        calls = []
        with patch.object(sys, "argv", ["bench.py", "all"]), \
             patch("bench.config.load_config", return_value=self.cfg), \
             patch("bench.datagen.generate.generate", side_effect=lambda *a, **k: calls.append("generate")), \
             patch("bench.indexes.create_indexes", side_effect=lambda *a: calls.append("index")), \
             patch("bench.verify.verify", side_effect=lambda *a: calls.append("verify") or True), \
             patch("bench.explain.explain", side_effect=lambda *a: calls.append("explain")), \
             patch("bench.runner.run_scenario", side_effect=lambda *a: calls.append("run") or [current]), \
             patch("bench.report.report", side_effect=lambda *a: calls.append("report")):
            bench_cli.main()
        self.assertEqual(calls, ["generate", "index", "verify", "explain", "run", "report"])

    def test_standalone_report_does_not_run_load_test(self):
        current = self.make_run("20260102_000000_popular_tag_r1")
        run = self.invoke("report", [current])
        run.assert_not_called()
        self.assertTrue((self.base / "report_popular_tag" / "report.md").is_file())

    def test_reports_interval_median_and_highest_peak_across_runs(self):
        from bench.report import report
        import pandas as pd

        for name, median, peak in [("20260101_000000_popular_tag_r1", 20, 40),
                                   ("20260102_000000_popular_tag_r2", 30, 80)]:
            run = self.make_run(name)
            (run / "steps.csv").write_text(
                "name,stage,users,requests,failures,rps,avg_rps,median_rps,max_rps,rps_samples,rps_exclude_seconds,avg_ms,p50_ms,p95_ms,p99_ms,max_ms\n"
                f"Aggregated,measure,10,100,0,10,15,{median},{peak},20,10,2,2,3,4,5\n"
            )
        report(self.cfg)
        out = self.base / "report_popular_tag"
        summary = pd.read_csv(out / "summary.csv").iloc[0]
        self.assertEqual(summary["avg_rps"], 15)
        self.assertEqual(summary["median_rps"], 25)
        self.assertEqual(summary["max_rps"], 80)
        self.assertIn("10s", (out / "report.md").read_text())

    def test_legacy_report_marks_interval_metrics_unavailable(self):
        from bench.report import report
        import pandas as pd

        self.make_run("20260101_000000_popular_tag_r1")
        report(self.cfg)
        out = self.base / "report_popular_tag"
        summary = pd.read_csv(out / "summary.csv").iloc[0]
        self.assertEqual(summary["avg_rps"], 10)
        self.assertTrue(pd.isna(summary["median_rps"]))
        self.assertTrue(pd.isna(summary["max_rps"]))
        text = (out / "report.md").read_text()
        self.assertIn("Legacy results", text)
        self.assertIn("N/A", text)

    def test_short_new_run_does_not_fall_back_to_untrimmed_rps(self):
        from bench.report import report
        import pandas as pd

        run = self.make_run("20260101_000000_popular_tag_r1")
        (run / "steps.csv").write_text(
            "name,stage,users,requests,failures,rps,avg_rps,median_rps,max_rps,rps_samples,rps_exclude_seconds,avg_ms,p50_ms,p95_ms,p99_ms,max_ms\n"
            "Aggregated,measure,10,100,0,10,,,,0,10,2,2,3,4,5\n"
        )
        report(self.cfg)
        summary = pd.read_csv(self.base / "report_popular_tag/summary.csv").iloc[0]
        self.assertTrue(pd.isna(summary["avg_rps"]))


if __name__ == "__main__":
    unittest.main()
