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

The run scripts take their checkouts from `OZONEDB_HOME`, `YCSB_CPP_DIR` (commit `8401bde`)
and `ROCKSDB_PREFIX`; the defaults are `~/ozonedb`, `~/YCSB-cpp` and `~/rocksdb-9.6.1`.
They also call shared helpers that stay in the repo:

- `bench/scripts/generate_workload.py`
- `bench/scripts/config/`
- `bench/scripts/local/run_local_ycsb.py`
- `bench/scripts/local/load_local_ycsb.py`
