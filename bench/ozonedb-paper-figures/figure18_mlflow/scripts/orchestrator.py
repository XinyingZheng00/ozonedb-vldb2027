#!/usr/bin/env python3
"""Per-trial analysis for the Figure 18 driver (run_ray_swapin.py).

analyze_trial() reads every trial_*.jsonl record a trial wrote (one line per
log_metric call) and returns error counts, error rates, throughput and
latency percentiles.
"""
import glob
import json
from pathlib import Path


def load_ops(trial_dir):
    ops = []
    for path in glob.glob(str(Path(trial_dir) / "*.jsonl")):
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    ops.append(json.loads(line))
                except json.JSONDecodeError:
                    pass  # partial/torn line -- flagged separately by caller if frequent
    return ops


def percentile(sorted_vals, p):
    if not sorted_vals:
        return None
    k = (len(sorted_vals) - 1) * p
    f = int(k)
    c = min(f + 1, len(sorted_vals) - 1)
    if f == c:
        return sorted_vals[f]
    return sorted_vals[f] + (sorted_vals[c] - sorted_vals[f]) * (k - f)


def classify_error(error_text):
    """Which SQL statement/table an MLflow write failure actually occurred
    on, parsed from the real exception text (not inferred from call
    structure) -- real, unmodified SqlAlchemyStore does the `metrics`
    history insert and the `latest_metrics` upsert as two separate
    statements in one call, so a single log_metric() failure needs the
    error text itself to say which one it was. Checks "latest_metrics"
    before the bare "metrics" table name, since the former contains the
    latter as a substring.
    """
    if not error_text:
        return "unknown"
    text = error_text.lower()
    if "latest_metrics" in text:
        return "latest_metrics"
    if "into metrics" in text or "metrics (" in text or '"metrics"' in text:
        return "metrics"
    return "other"


def analyze_trial(trial_dir, wall_s):
    ops = load_ops(trial_dir)
    total = len(ops)
    oks = [o for o in ops if o.get("ok")]
    fails = [o for o in ops if not o.get("ok")]
    lat_ms = sorted(o["latency_us"] / 1000.0 for o in oks)
    # Pure training-compute time (partial_fit/predict_proba/log_loss) --
    # only present on the main training thread's step records (ray_trainable.py),
    # not on monitor-thread records, and identical code regardless of
    # backend, so any difference across backends reflects host resource
    # contention (e.g. a client-server backend's own processes competing
    # for CPU), not the training code itself.
    compute_lat_ms = sorted(o["compute_latency_us"] / 1000.0 for o in oks if "compute_latency_us" in o)
    error_counts = {}
    error_counts_by_category = {}
    for o in fails:
        # Grouping key capped at 160 chars deliberately -- ray_trainable.py
        # captures up to 600 chars per error (needed for classify_error()
        # to see the failing SQL statement), but full error text includes
        # per-call parameters (values/timestamps/run_uuid), making every
        # instance's full text unique. Grouping on the full text blew up
        # error_counts to one entry per failure (hundreds of near-duplicate
        # ~600-char keys), which broke CSV serialization outright (a real
        # field-size-limit error, not just an unwieldy report) -- 160 chars
        # keeps the generic error shape (exception type + "database is
        # locked" etc.) without the per-call parameter tail.
        error_text = o.get("error", "?")
        error_counts[error_text[:160]] = error_counts.get(error_text[:160], 0) + 1
        category = classify_error(error_text)
        error_counts_by_category[category] = error_counts_by_category.get(category, 0) + 1

    fails_metrics_table = error_counts_by_category.get("metrics", 0) + error_counts_by_category.get("unknown", 0)

    summary = {
        "total_ops": total,
        "ok_ops": len(oks),
        "failed_ops": len(fails),
        "error_rate": (len(fails) / total) if total else None,
        "error_counts": error_counts,
        "error_counts_by_category": error_counts_by_category,
        # Error rate counting only failures on the `metrics` history write
        # (the operation actually being swapped between backends) --
        # excludes `latest_metrics` failures, which happen on real SQL for
        # every backend including ozonedb and aren't part of what's being
        # compared. "unknown"-category failures (message didn't match
        # either pattern) are counted here rather than silently dropped,
        # so this number is a ceiling, not an undercount.
        "error_rate_metrics_only": (fails_metrics_table / total) if total else None,
        "throughput_ops_sec": total / wall_s if wall_s > 0 else None,
        "successful_throughput_ops_sec": len(oks) / wall_s if wall_s > 0 else None,
        "p50_latency_ms": percentile(lat_ms, 0.50),
        "p95_latency_ms": percentile(lat_ms, 0.95),
        "p99_latency_ms": percentile(lat_ms, 0.99),
        "mean_latency_ms": (sum(lat_ms) / len(lat_ms)) if lat_ms else None,
        "p50_compute_latency_ms": percentile(compute_lat_ms, 0.50),
        "p99_compute_latency_ms": percentile(compute_lat_ms, 0.99),
        "mean_compute_latency_ms": (sum(compute_lat_ms) / len(compute_lat_ms)) if compute_lat_ms else None,
    }
    return summary, ops
