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


if __name__ == "__main__":
    unittest.main()
