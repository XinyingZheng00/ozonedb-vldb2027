# Figure 17: Serval Mesh (Rhizome) use case

Paper: `images/experiment/serval_combined.pdf`, `\label{fig:usecase-serval}` (§5 Use cases).

```
run_fig17.sh                                   build servald with OzoneDB, run the sweep, draw the plot
scripts/run_persistent_adder_benchmark.py      benchmark driver
scripts/rest_worker.py                         REST client used by the sqlite_rest backend
scripts/config_template.py                     writes the OzoneDB config for each trial
scripts/plot_serval_combined.py                paper plot script (throughput + mean add() latency)
scripts/plot_common.py, plot_throughput.py, plot_mean_latency.py, plot_p99_latency.py, plot_architecture.py
                                               shared plot code and single-panel / architecture plots (not in the paper)
data/paper_data/persistent_adder_results/trials.csv   the paper's data (3 repeats per backend and N)
data/paper_data/analysis.md, full_run.log      the paper run's write-up and console log
data/reproduced_data/20261024/                 rerun on amd133 (c6525-25g): trials.csv, run.log
plots/paper_figure.pdf                         the figure in the paper
plots/reproduced_figure_20261024.pdf
plots/architecture.pdf, p99_add_latency.pdf    architecture diagram and p99 add() latency from the paper's data (not in the paper)
```

## The system under test: `bench/usecases/serval-dna/` (submodule)

`ozonedb/bench/usecases/serval-dna/` is a git submodule pointing at
[XinyingZheng00/serval-dna](https://github.com/XinyingZheng00/serval-dna), branch
`ozonedb-backend`: upstream [servalproject/serval-dna](https://github.com/servalproject/serval-dna)
at commit `029adc1`, plus one commit that adds the OzoneDB backend. Fetch it with
`git submodule update --init` (`run_fig17.sh` does this if it's missing). To see
every change against upstream: `git -C bench/usecases/serval-dna diff 029adc1`.

The three backends run the same `servald` binary:

| Backend | Who writes | Store |
|---|---|---|
| `sqlite` | 1 daemon (adds a bundle every 100 ms) + N CLI processes (`rhizome add`/`extract file`) | one SQLite file opened by all N+1 processes |
| `ozonedb` | same as `sqlite` | OzoneDB (multi-process mode), selected by `RHIZOME_OZONEDB_CONFIG` |
| `sqlite_rest` | N+1 HTTP clients (`scripts/rest_worker.py`) to the daemon's REST API | one SQLite file opened by the daemon only (the re-architected design) |

## Rerun the experiment (Linux node with OzoneDB built)

```bash
bash run_fig17.sh     # -> data/reproduced_data/<date>/, plots/reproduced_figure_<date>.pdf
```

It fetches the `serval-dna` submodule if needed, builds `servald` there if it
isn't already linked to this node's OzoneDB,
then runs the paper's sweep: N = 1-32 CLI workers racing one daemon, 30 s per
trial, persistent adder every 100 ms, backends sqlite, ozonedb, sqlite_rest,
3 repeats. About an hour. The per-trial `servald` instance folders (about 2 GB)
are left in the output folder on the node; the plot only needs `trials.csv`.

## Redraw a plot

```bash
cd scripts
python3 plot_serval_combined.py --out <file>          # reads the paper's trials.csv by default (= paper_figure.pdf)
python3 plot_serval_combined.py --csv ../data/reproduced_data/20261024/trials.csv --out <file>
```

Without `--out`, plot scripts write into `plots/`. Needs matplotlib.
