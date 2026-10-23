# OzoneDB Paper Figures

Each folder holds one figure or table: the paper's PDF (`paper_figure*.pdf`), its
data, its scripts, and a `README.md` with the commands to rebuild the plot and
regenerate the data.

| Figure | Owner | Data | Script | Producibility |
| --- | --- | :---: | :---: | :---: |
| [figure1a_first_generation](figure1a_first_generation) | Oliver | | | |
| [figure1b_time_breakdown](figure1b_time_breakdown) | Oliver | | | |
| [figure2a_sqlite_workloadd](figure2a_sqlite_workloadd) | Xinying | ☑ | ☑ | ☑ |
| [figure2b_slatedb_await_durable_bar](figure2b_slatedb_await_durable_bar) | Oliver | | | |
| [figure9_multi_process_writer](figure9_multi_process_writer) | Xinying | ☑ | ☑ | ☑ |
| [figure10_multi_node_writer](figure10_multi_node_writer) | Oliver | | | |
| [figure11_visibility_scaling](figure11_visibility_scaling) | Oliver | | | |
| [figure12_cost](figure12_cost) | Oliver | | | |
| [figure13_dist_compaction](figure13_dist_compaction) | Xinying | ☑ | ☑ | ☑ |
| [figure14ab_writer_activity](figure14ab_writer_activity) | Xinying | ☑ | ☑ | ☑ |
| [table4_reassignment](table4_reassignment) | Xinying | ☑ | ☑ | ☑ |
| [figure15_generation_dist](figure15_generation_dist) | Xinying | ☑ | ☑ | ☑ |
| [figure16_append_pagecache](figure16_append_pagecache) | Xinying | ◐ | ☑ | ◐ |
| [figure17_serval](figure17_serval) | Xinying | ☑ | ☑ | ☑ |
| [figure18_mlflow](figure18_mlflow) | Xinying | ☑ | ☑ | ☑ |
| [figure19a_single_writer_local](figure19a_single_writer_local) | Xinying | ☑ | ☑ | ☑ |
| [figure19b_single_writer_throughput](figure19b_single_writer_throughput) | Oliver | | | |

☑ yes · ◐ partial · ☐ no. **Producibility** means the plot can be rebuilt from
this folder alone; every ☑ was checked by running its README's command.
Oliver's rows are left blank for him to fill in.

Notes on Xinying's figures:

- **figure16:** the original script and data were lost. The benchmark, sweep and plot script were rebuilt from §5.6, and the paper's values were read back from its PDF into `data/paper_values.csv`, so the plot redraws. New data needs a rerun on a CloudLab c6525-25g node (`scripts/run_on_cloudlab.sh`).
- **figure18:** the original combining script was lost; `usecases/mlflow/plots/plot_combined.py` was rebuilt from the paper's PDF and reproduces it exactly.
- **table4:** a table, not a plot. Its README has a short snippet that recomputes the numbers from `trials.csv`.

The run scripts use the CloudLab paths `/users/Xinying/ozonedb`, `/users/Xinying/YCSB-cpp`
(commit `8401bde`) and `/users/Xinying/rocksdb-9.6.1`. They also call shared helpers
that stay in the repo: `bench/scripts/generate_workload.py`, `bench/scripts/config/`,
`bench/scripts/local/run_local_ycsb.py` and `load_local_ycsb.py`.
