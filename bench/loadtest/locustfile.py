import csv
import logging
import os
import random
import time
from pathlib import Path

from locust import LoadTestShape, User, constant, events, task

from bench import db, scenarios
from bench.config import load_config
from bench.throughput import summarize_rps

CFG = load_config(os.environ["BENCH_CONFIG"])
RUN_DIR = Path(os.environ["BENCH_RUN_DIR"])
MIX = scenarios.resolve_mix(CFG)
NAMES = [n for n, _ in MIX]
WEIGHTS = [w for _, w in MIX]
COLLS = {n: scenarios.target_collection(CFG, n) for n in NAMES}

FIELDS = ["stage", "users", "name", "duration_s", "requests", "failures",
          "rps", "avg_rps", "median_rps", "max_rps", "rps_samples", "rps_exclude_seconds",
          "avg_ms", "p50_ms", "p95_ms", "p99_ms", "max_ms"]
RPS_FIELDS = ["stage", "users", "name", "timestamp", "elapsed_s", "rps"]


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
    """Constant users: discard warm-up, then save measurement after final worker reports."""

    def __init__(self):
        super().__init__()
        load = CFG.load
        self.stages = []
        if load.warmup_seconds > 0:
            self.stages.append(("warmup", load.users, load.warmup_seconds))
        self.stages.append(("measure", load.users, load.duration_seconds))
        self.cur = None
        self.stage_start = 0.0
        self.stage_wall_start = 0.0
        self.done = False
        self.out = RUN_DIR / "steps.csv"
        self.pending_snapshot = None

    def _index(self, t):
        acc = 0
        for i, (_, _, d) in enumerate(self.stages):
            acc += d
            if t < acc:
                return i
        return None

    def _snapshot(self, idx, now, wall_end=None):
        runner = self.runner
        if runner is None:
            return
        label, users, _ = self.stages[idx]
        if label != "warmup":
            new = not self.out.exists()
            dur = max(now - self.stage_start, 1e-9)
            rows = [("Aggregated", runner.stats.total)] + [(k[0], e) for k, e in runner.stats.entries.items()]
            wall_end = wall_end if wall_end is not None else time.time()
            sample_out = RUN_DIR / "rps.csv"
            new_samples = not sample_out.exists()
            with self.out.open("a", newline="") as f, sample_out.open("a", newline="") as sf:
                w = csv.DictWriter(f, FIELDS)
                sw = csv.DictWriter(sf, RPS_FIELDS)
                if new:
                    w.writeheader()
                if new_samples:
                    sw.writeheader()
                for name, e in rows:
                    throughput, samples = summarize_rps(
                        e.num_reqs_per_sec, self.stage_wall_start, wall_end,
                        CFG.load.rps_exclude_seconds,
                    )
                    for second, count in samples:
                        sw.writerow({
                            "stage": label, "users": users, "name": name,
                            "timestamp": second,
                            "elapsed_s": round(second - self.stage_wall_start, 3), "rps": count,
                        })
                    w.writerow({
                        "stage": label, "users": users, "name": name, "duration_s": round(dur, 1),
                        "requests": e.num_requests, "failures": e.num_failures,
                        "rps": round(e.num_requests / dur, 2),
                        **throughput,
                        "avg_ms": round(e.avg_response_time, 2),
                        "p50_ms": e.get_response_time_percentile(0.50),
                        "p95_ms": e.get_response_time_percentile(0.95),
                        "p99_ms": e.get_response_time_percentile(0.99),
                        "max_ms": round(e.max_response_time or 0, 2),
                    })
            if not samples:
                logging.warning("Measured stage has no complete RPS samples after excluding %ss; throughput is unavailable.",
                                CFG.load.rps_exclude_seconds)
        runner.stats.reset_all()

    def finish(self):
        # Headless Locust quits its runner before firing quitting, allowing final
        # distributed reports to arrive. Keep the original stage end as cutoff.
        if self.pending_snapshot is not None:
            idx, now, wall_end = self.pending_snapshot
            self.pending_snapshot = None
            self._snapshot(idx, now, wall_end)

    def tick(self):
        if self.done:
            return None
        t = self.get_run_time()
        idx = self._index(t)
        if idx != self.cur:
            if self.cur is not None:
                if idx is None:
                    self.pending_snapshot = (self.cur, t, time.time())
                else:
                    self._snapshot(self.cur, t)
                if idx is not None and self.stages[idx][0] == "measure" and self.runner:
                    actual = self.runner.user_count
                    if actual < CFG.load.users:
                        logging.warning(
                            "Warm-up ended with %s of %s target users; measured results include ramp-up.",
                            actual, CFG.load.users,
                        )
            self.cur = idx
            if idx is not None:
                self.stage_start = t
                self.stage_wall_start = time.time()
        if idx is None:
            self.done = True
            return None
        _, users, _ = self.stages[idx]
        return users, CFG.load.spawn_rate


@events.quitting.add_listener
def save_final_snapshot(environment, **kwargs):
    shape = environment.shape_class
    if isinstance(shape, StepShape):
        shape.finish()
