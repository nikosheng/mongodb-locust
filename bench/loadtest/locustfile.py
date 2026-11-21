import csv
import logging
import os
import random
import time
from pathlib import Path

from locust import LoadTestShape, User, constant, events, task

from bench import db, scenarios
from bench.config import load_config

CFG = load_config(os.environ["BENCH_CONFIG"])
RUN_DIR = Path(os.environ["BENCH_RUN_DIR"])
MIX = scenarios.resolve_mix(CFG)
NAMES = [n for n, _ in MIX]
WEIGHTS = [w for _, w in MIX]
COLLS = {n: scenarios.target_collection(CFG, n) for n in NAMES}

FIELDS = ["stage", "users", "name", "duration_s", "requests", "failures",
          "rps", "avg_ms", "p50_ms", "p95_ms", "p99_ms", "max_ms"]


class AdvancedSearchUser(User):
    wait_time = constant(CFG.load.think_time_ms / 1000)

    def on_start(self):
        self.rng = random.Random()
        self.db = db.get_db(CFG)

    @task
    def search(self):
        name = self.rng.choices(NAMES, WEIGHTS)[0]
        pipeline = scenarios.build(CFG, name, self.rng)
        start = time.perf_counter()
        exc, size = None, 0
        try:
            size = len(list(self.db[COLLS[name]].aggregate(pipeline)))
        except Exception as e:  # noqa: BLE001
            exc = e
        self.environment.events.request.fire(
            request_type="AGG",
            name=name,
            response_time=(time.perf_counter() - start) * 1000,
            response_length=size,
            exception=exc,
            context={},
        )


class StepShape(LoadTestShape):
    """Constant users: warm-up stage, then one measured stage; snapshots and resets stats at each boundary."""

    def __init__(self):
        super().__init__()
        load = CFG.load
        self.stages = []
        if load.warmup_seconds > 0:
            self.stages.append(("warmup", load.users, load.warmup_seconds))
        self.stages.append(("measure", load.users, load.duration_seconds))
        self.cur = None
        self.stage_start = 0.0
        self.done = False
        self.out = RUN_DIR / "steps.csv"

    def _index(self, t):
        acc = 0
        for i, (_, _, d) in enumerate(self.stages):
            acc += d
            if t < acc:
                return i
        return None

    def _snapshot(self, idx, now):
        runner = self.runner
        if runner is None:
            return
        label, users, _ = self.stages[idx]
        if label != "warmup":
            new = not self.out.exists()
            dur = max(now - self.stage_start, 1e-9)
            rows = [("Aggregated", runner.stats.total)] + [(k[0], e) for k, e in runner.stats.entries.items()]
            with self.out.open("a", newline="") as f:
                w = csv.DictWriter(f, FIELDS)
                if new:
                    w.writeheader()
                for name, e in rows:
                    w.writerow({
                        "stage": label, "users": users, "name": name, "duration_s": round(dur, 1),
                        "requests": e.num_requests, "failures": e.num_failures,
                        "rps": round(e.num_requests / dur, 2),
                        "avg_ms": round(e.avg_response_time, 2),
                        "p50_ms": e.get_response_time_percentile(0.50),
                        "p95_ms": e.get_response_time_percentile(0.95),
                        "p99_ms": e.get_response_time_percentile(0.99),
                        "max_ms": round(e.max_response_time or 0, 2),
                    })
        runner.stats.reset_all()

    def tick(self):
        if self.done:
            return None
        t = self.get_run_time()
        idx = self._index(t)
        if idx != self.cur:
            if self.cur is not None:
                self._snapshot(self.cur, t)
                if idx is not None and self.stages[idx][0] == "measure" and self.runner:
                    actual = self.runner.user_count
                    if actual < CFG.load.users:
                        logging.warning(
                            "Warm-up ended with %s of %s target users; measured results include ramp-up.",
                            actual, CFG.load.users,
                        )
            self.cur, self.stage_start = idx, t
        if idx is None:
            self.done = True
            return None
        _, users, _ = self.stages[idx]
        return users, CFG.load.spawn_rate
