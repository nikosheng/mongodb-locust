#!/usr/bin/env python3
"""Single entry point. Everything is driven by benchmark.yaml."""
import argparse
import sys


def main():
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--config", default="benchmark.yaml")
    common.add_argument("--scenario", help="override scenario.active")
    common.add_argument("--customers", type=int, help="override data.customers")
    common.add_argument("--repeats", type=int, help="override load.repeats")
    common.add_argument("--users", type=int, help="override load.users")
    common.add_argument("--duration", type=int, help="override load.duration_seconds")

    p = argparse.ArgumentParser(description="MongoDB $lookup benchmark")
    sub = p.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("generate", parents=[common], help="generate and load Faker data")
    g.add_argument("--drop", action="store_true", help="drop and rebuild collections")
    sub.add_parser("index", parents=[common], help="create indexes")
    sub.add_parser("verify", parents=[common], help="check data, indexes, tag distribution")
    sub.add_parser("explain", parents=[common], help="explain executionStats for the active scenario")
    sub.add_parser("run", parents=[common], help="run the Locust load test")
    sub.add_parser("report", parents=[common], help="build report from results")
    a = sub.add_parser("all", parents=[common], help="generate, index, verify, explain, run, report")
    a.add_argument("--drop", action="store_true")
    args = p.parse_args()

    from bench.config import load_config
    cfg = load_config(args.config, {
        "scenario.active": args.scenario,
        "data.customers": args.customers,
        "load.repeats": args.repeats,
        "load.users": args.users,
        "load.duration_seconds": args.duration,
    })

    def do_generate():
        from bench.datagen.generate import generate
        generate(cfg, drop=getattr(args, "drop", False))

    def do_index():
        from bench.indexes import create_indexes
        create_indexes(cfg)

    def do_verify():
        from bench.verify import verify
        return verify(cfg)

    def do_explain():
        from bench.explain import explain
        explain(cfg)

    def do_run():
        from bench.runner import run_scenario
        run_scenario(cfg)

    def do_report():
        from bench.report import report
        report(cfg)

    if args.cmd == "generate":
        do_generate()
    elif args.cmd == "index":
        do_index()
    elif args.cmd == "verify":
        sys.exit(0 if do_verify() else 1)
    elif args.cmd == "explain":
        do_explain()
    elif args.cmd == "run":
        do_run()
    elif args.cmd == "report":
        do_report()
    elif args.cmd == "all":
        do_generate()
        do_index()
        if not do_verify():
            sys.exit("verify failed")
        do_explain()
        do_run()
        do_report()


if __name__ == "__main__":
    main()
