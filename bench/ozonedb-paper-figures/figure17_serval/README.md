# Figure 17: Serval Mesh (Rhizome) use case

Paper: `images/experiment/serval_combined.pdf`, `\label{fig:usecase-serval}` (§5 Use cases).

| File | What it is |
|---|---|
| `paper_figure.pdf` | the figure as it appears in the paper (identical to `serval-audit/plots/serval_combined.pdf`) |
| `serval-audit/persistent_adder_results/trials.csv` | results: throughput (cycles/s) and add() latency per backend and worker count, 3 repeats |
| `serval-audit/plots/plot_serval_combined.py` | paper plot script (throughput + mean add() latency side by side) |
| `serval-audit/plots/plot_*.py` | single-panel and architecture plots (not in the paper) |
| `serval-audit/run_persistent_adder_benchmark.py`, `rest_worker.py` | benchmark driver and REST worker |
| `serval-audit/analysis.md` | write-up and exact run commands |
| `ozonedb/bench/usecases/serval/serval-dna/` (outside this folder) | the system under test: upstream serval-dna (commit `029adc1`) with OzoneDB wired into Rhizome. It adds `ozonedb_shim.cpp`/`.h` and `rhizome_ozonedb_bridge.c`/`.h`, and modifies `rhizome_database.c`, `rhizome_store.c`, `server.c` and `sourcefiles.mk`. The benchmark runs the `servald` built there. |
| `usecases/config_template.py` | writes the OzoneDB config for each trial; imported by the driver |
| `usecases/orchestrator.py` | harness from the earlier synthetic phases; it launched a `kv_writer` binary that has since been removed. The figure doesn't use it. |

## Reproduce the plot (verified)

```bash
python3 serval-audit/plots/plot_serval_combined.py
# reads serval-audit/persistent_adder_results/trials.csv,
# writes serval-audit/plots/serval_combined.pdf (the paper figure)
```

`--csv` and `--out` override the paths. Needs matplotlib.

## Regenerate the data (Linux node with serval-dna built)

The driver runs `$OZONEDB_HOME/bench/usecases/serval/serval-dna/servald`
(`OZONEDB_HOME` defaults to `~/ozonedb`), so build serval-dna there first.

```bash
cd serval-audit
python3 run_persistent_adder_benchmark.py \
  --workers 1,2,4,8,16,20,24,28,32 --duration-s 30 \
  --adder-interval-ms 100 --backends sqlite,ozonedb,sqlite_rest \
  --repeats 3 --out-dir persistent_adder_results
```

See `serval-audit/analysis.md` for setup.
