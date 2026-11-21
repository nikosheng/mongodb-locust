import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import pymongo
import yaml

from bench import db

ROOT = Path(__file__).resolve().parent.parent


def run_scenario(cfg, repeats=None):
    repeats = repeats or cfg.load.repeats
    database = db.get_db(cfg)
    meta = database["_bench_meta"].find_one({"_id": "fingerprint"})
    if not meta or meta.get("fingerprint") != cfg.data.fingerprint():
        print("WARNING: loaded data does not match this config (run `generate --drop`). Continuing anyway.")
    build = db.get_client(cfg).server_info()

    base = Path(cfg.output.dir)
    runs = []
    for r in range(1, repeats + 1):
        run_dir = base / f"{datetime.now():%Y%m%d_%H%M%S}_{cfg.scenario.active}_r{r}"
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "config.effective.yaml").write_text(yaml.safe_dump(cfg.raw, sort_keys=False))
        (run_dir / "meta.json").write_text(json.dumps({
            "scenario": cfg.scenario.active,
            "customers": cfg.data.customers,
            "server_version": build.get("version"),
            "pymongo": pymongo.version,
            "repeat": r,
            "started": datetime.now().isoformat(),
        }, indent=2))
        env = dict(os.environ,
                   BENCH_CONFIG=str((run_dir / "config.effective.yaml").resolve()),
                   BENCH_RUN_DIR=str(run_dir.resolve()),
                   PYTHONPATH=str(ROOT))
        cmd = [sys.executable, "-m", "locust", "-f", str(Path(__file__).resolve().parent / "loadtest" / "locustfile.py"),
               "--headless", "--processes", str(cfg.load.workers), "--loglevel", "WARNING"]
        print(f"[run {r}/{repeats}] scenario={cfg.scenario.active}, "
              f"automatic warm-up={cfg.load.warmup_seconds}s -> {run_dir}", flush=True)
        subprocess.run(cmd, env=env, cwd=str(ROOT), check=False)
        runs.append(run_dir)
    return runs
