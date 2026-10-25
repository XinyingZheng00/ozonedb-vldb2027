# OzoneDB Paper Figures

Each folder holds one figure or table. The folder contains:

- the paper's PDF (`paper_figure*.pdf`)
- its data
- its scripts
- a `README.md` with the commands that rebuild the plot and regenerate the data

| Figure | Owner | Data | Script | Producibility |
| --- | --- | :---: | :---: | :---: |
| [figure1a_first_generation](figure1a_first_generation) | Oliver | ☑ | ☑ | ☑ |
| [figure1b_time_breakdown](figure1b_time_breakdown) | Oliver | ☑ | ☑ | ☑ |
| [figure2a_sqlite_workloadd](figure2a_sqlite_workloadd) | Xinying | ☑ | ☑ | ☑ |
| [figure2b_slatedb_await_durable_bar](figure2b_slatedb_await_durable_bar) | Oliver | ☑ | ☑ | ☑ |
| [figure9_multi_process_writer](figure9_multi_process_writer) | Xinying | ☑ | ☑ | ☑ |
| [figure10_multi_node_writer](figure10_multi_node_writer) | Oliver | ☑ | ☑ | ☑ |
| [figure11_visibility_scaling](figure11_visibility_scaling) | Oliver | ☑ | ☑ | ☑ |
| [figure12_cost](figure12_cost) | Oliver | ☑ | ☑ | ☑ |
| [figure13_dist_compaction](figure13_dist_compaction) | Xinying | ☑ | ☑ | ☑ |
| [figure14ab_writer_activity](figure14ab_writer_activity) | Xinying | ☑ | ☑ | ☑ |
| [table4_reassignment](table4_reassignment) | Xinying | ☑ | ☑ | ☑ |
| [figure15_generation_dist](figure15_generation_dist) | Xinying | ☑ | ☑ | ☑ |
| [figure16_append_pagecache](figure16_append_pagecache) | Xinying | ◐ | ☑ | ◐ |
| [figure17_serval](figure17_serval) | Xinying | ☑ | ☑ | ☑ |
| [figure18_mlflow](figure18_mlflow) | Xinying | ☑ | ☑ | ☑ |
| [figure19a_single_writer_local](figure19a_single_writer_local) | Xinying | ☑ | ☑ | ☑ |
| [figure19b_single_writer_throughput](figure19b_single_writer_throughput) | Oliver | ☑ | ☑ | ☑ |

☑ yes · ◐ partial · ☐ no. **Producibility** means that you can rebuild the plot from
this folder alone. Each ☑ was checked with the command in its README.

Notes on Xinying's figures:

- **figure16:** the original script and data were lost. The benchmark, the sweep and the plot script were rebuilt from §5.6. The paper's values were read back from its PDF into `data/paper_values.csv`, so the plot redraws. New data needs a rerun on a CloudLab c6525-25g node (`scripts/run_on_cloudlab.sh`).
- **figure18:** the original combining script was lost. `usecases/mlflow/plots/plot_combined.py` was rebuilt from the paper's PDF. It reproduces the figure exactly.
- **table4:** this is a table, not a plot. Its README has a short snippet that recomputes the numbers from `trials.csv`.

The run scripts take their checkouts from `OZONEDB_HOME`, `YCSB_CPP_DIR` (commit `8401bde`)
and `ROCKSDB_PREFIX`; the defaults are `~/ozonedb`, `~/YCSB-cpp` and `~/rocksdb-9.6.1`.
They also call shared helpers
that stay in the repo:

- `bench/scripts/generate_workload.py`
- `bench/scripts/config/`
- `bench/scripts/local/run_local_ycsb.py`
- `bench/scripts/local/load_local_ycsb.py`

Notes on Oliver's figures:

- The folders were filled from the artifact repository (`ozonekv-artifact/figures/`, 2026-10-23). They use the same `data/` and `scripts/` layout. The run scripts under `scripts/ozonekv/` and `scripts/cassandra/` of figures 10, 11, 12 and 19b are copies of this repo's `bench/scripts/` as of 2026-10-21. The folders `scripts/crsqlite/` and `scripts/slatedb/` come from the crsqlite repo and the SlateDB fork.
- **figure1b:** the perf profiles behind `time_breakdown.csv` were not kept. The CSV is shipped as is.
- **figure10:** the raw per-writer result files of the two 100 GB campaigns were not kept. The extracted TSVs are the data. SlateDB stays the 1 GB campaign. `scripts/extract_steady_throughput.py` drops the dead-tail and cleanup samples of YCSB. `bench/scripts/` has the same copy.
- **figure11:** the raw run directories are 37 MB. The plot reads only `visibility.csv`. The paper's text quotes a 1 ms OzoneKV median. The data gives 3.2 to 4.1 ms. The 16× claim holds.
- **figure12:** the raw result directories were not kept. The TSV was extracted from them. `scripts/paper_cost_figure.py` is the paper's version. `bench/scripts/plot/` has the same copy.
- **figure19b:** the paper's copy has the legend outside the axes. The script draws it inside the axes. The bars are the same.
- The campaign chains of figures 10 and 12 are the scripts as run. The CloudLab login and hostnames are replaced by `${NODE_*}` variables. `OZONEDB_HOME` holds a laptop worktree path. Before a rerun, edit both.
