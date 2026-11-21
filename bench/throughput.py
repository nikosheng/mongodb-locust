"""Throughput from complete, wall-clock-aligned one-second Locust buckets."""

import math
from statistics import median


def summarize_rps(counts, start, end, exclude_seconds=10):
    # Partial boundary seconds are not comparable with complete seconds.
    first = math.ceil(start + exclude_seconds)
    stop = math.floor(end)
    samples = [(second, counts.get(second, 0)) for second in range(first, stop)]
    values = [count for _, count in samples]
    return {
        "avg_rps": round(sum(values) / len(values), 2) if values else None,
        "median_rps": round(median(values), 2) if values else None,
        "max_rps": max(values) if values else None,
        "rps_samples": len(values),
        "rps_exclude_seconds": exclude_seconds,
    }, samples
