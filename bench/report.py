import json
from pathlib import Path

import pandas as pd


def report(cfg, scenario=None):
    scenario = scenario or cfg.scenario.active
    base = Path(cfg.output.dir)
    dirs = sorted(d for d in base.glob(f"*_{scenario}_r*") if (d / "steps.csv").exists())
    if not dirs:
        raise SystemExit(f"No results found for scenario '{scenario}' in {base}")
    df = pd.concat([pd.read_csv(d / "steps.csv").assign(run=d.name) for d in dirs])
    meta = json.loads((dirs[-1] / "meta.json").read_text())

    num = ["requests", "failures", "rps", "avg_ms", "p50_ms", "p95_ms", "p99_ms", "max_ms"]
    agg = df.groupby(["name", "stage", "users"], as_index=False)[num].median()
    agg["err_rate"] = (agg["failures"] / agg["requests"].clip(lower=1)).round(5)

    total = agg[agg["name"] == "Aggregated"].sort_values("users")
    lines = [
        f"# Benchmark report: {scenario}",
        "",
        f"- Server: MongoDB {meta['server_version']}  |  pymongo {meta['pymongo']}  |  customers: {meta['customers']:,}",
        f"- Runs: {len(dirs)} (values are medians across runs)",
        "",
        "## Aggregate (steady state)",
        "",
        total[["users", "requests", "rps", "avg_ms", "p50_ms", "p95_ms", "p99_ms", "max_ms", "err_rate"]]
        .to_markdown(index=False),
        "",
    ]
    if (agg["name"] != "Aggregated").any():
        lines += ["## Per query shape (p95 ms)", ""]
        piv = agg[agg["name"] != "Aggregated"].pivot(index="users", columns="name", values="p95_ms")
        lines += [piv.to_markdown(), ""]

    th = cfg.thresholds
    if th.p95_ms is not None or th.max_error_rate is not None:
        lines += ["## Thresholds", ""]
        for _, r in total.iterrows():
            checks = []
            if th.p95_ms is not None:
                checks.append(f"p95 {r.p95_ms:.0f}ms <= {th.p95_ms:.0f}ms: {'PASS' if r.p95_ms <= th.p95_ms else 'FAIL'}")
            if th.max_error_rate is not None:
                checks.append(f"errors {r.err_rate:.4%} <= {th.max_error_rate:.4%}: {'PASS' if r.err_rate <= th.max_error_rate else 'FAIL'}")
            lines.append(f"- {int(r.users)} users: " + "; ".join(checks))
        lines.append("")

    out = base / f"report_{scenario}"
    out.mkdir(parents=True, exist_ok=True)

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    names = [n for n in agg["name"].unique()]
    multi = agg["users"].nunique() > 1
    fig, ax = plt.subplots(figsize=(9, 5))
    if multi:
        for name, g in agg.groupby("name"):
            g = g.sort_values("users")
            ax.plot(g["users"], g["p95_ms"], marker="o", label=name, linewidth=2.5 if name == "Aggregated" else 1)
        ax.set_xlabel("concurrent users")
        ax.set_ylabel("p95 latency (ms)")
        ax.set_title(f"{scenario}: p95 vs concurrent users")
        ax.legend(fontsize=8)
    else:
        sub = agg.set_index("name").loc[names]
        x = range(len(names))
        w = 0.27
        for k, (col, lab) in enumerate([("p50_ms", "p50"), ("p95_ms", "p95"), ("p99_ms", "p99")]):
            ax.bar([i + (k - 1) * w for i in x], sub[col], w, label=lab)
        ax.set_xticks(list(x))
        ax.set_xticklabels(names, rotation=30, ha="right", fontsize=8)
        ax.set_ylabel("latency (ms)")
        ax.set_title(f"{scenario}: latency at {int(agg['users'].iloc[0])} concurrent users")
        ax.legend()
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(out / "latency.png", dpi=120)

    (out / "report.md").write_text("\n".join(lines))
    agg.to_csv(out / "summary.csv", index=False)
    print("\n".join(lines))
    print(f"Report written to {out}")
