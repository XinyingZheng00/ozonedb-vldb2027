# Table 4: reassignment count under churn

Paper: `\label{tab:reassignment}` (§5, Churn Recovery). The numbers are typed into the `.tex` file.

| Abort ratio | 1% | 5% | 10% | 25% | 50% |
|---|---|---|---|---|---|
| Avg. reassignments | 8.8 | 44.4 | 87.0 | 211.8 | 401.6 |

| File | What it is |
|---|---|
| `data/v4_20260501T003206/trials.csv` | one row per trial (4 writers, abort ratio 1-50%, 5 trials each) |
| `data/v4_20260501T003206/per_trial/`, `run_config.json`, `plots/` | per-trial logs, configuration, plots |
| `scripts/` | same runner and plot scripts as figures 13 and 15 |

## Reproduce the table (verified)

Each value is the mean of `n_tasks_with_reassignment` over the 5 trials at that abort ratio:

```bash
python3 - <<'EOF'
import csv, collections, statistics
g = collections.defaultdict(list)
for r in csv.DictReader(open("data/v4_20260501T003206/trials.csv")):
    if r["vary"] == "abort":
        g[float(r["abort_rate"])].append(int(r["n_tasks_with_reassignment"]))
for a in sorted(g):
    print(f"{a:.0%}\t{statistics.mean(g[a]):.1f}")
EOF
```

This prints 8.8, 44.4, 87.0, 211.8 and 401.6.

## Regenerate the data

```bash
python3 scripts/churn_runner.py --vary abort
```

See `figure13_dist_compaction/README.md` for setup.
