import json
import random
from pathlib import Path

from bson import json_util

from bench import db, scenarios

KEYS = {"executionTimeMillis", "totalDocsExamined", "totalKeysExamined", "strategy", "indexesUsed"}


def _walk(node, found):
    if isinstance(node, dict):
        for k, v in node.items():
            if k in KEYS:
                found.setdefault(k, []).append(v)
            _walk(v, found)
    elif isinstance(node, list):
        for v in node:
            _walk(v, found)


def explain(cfg, out_dir=None):
    database = db.get_db(cfg)
    out = Path(out_dir or cfg.output.dir) / "explain"
    out.mkdir(parents=True, exist_ok=True)
    rng = random.Random(cfg.data.seed)
    for name, _ in scenarios.resolve_mix(cfg):
        pipeline = scenarios.build(cfg, name, rng)
        coll = scenarios.target_collection(cfg, name)
        res = database.command({
            "explain": {"aggregate": coll, "pipeline": pipeline, "cursor": {}},
            "verbosity": "executionStats",
        })
        (out / f"{name}.json").write_text(json_util.dumps(res, indent=2))
        found = {}
        _walk(json.loads(json_util.dumps(res)), found)
        print(f"== {name} (on {coll})")
        for k, v in found.items():
            print(f"   {k}: {v}")
    print(f"Full explain output written to {out}")
