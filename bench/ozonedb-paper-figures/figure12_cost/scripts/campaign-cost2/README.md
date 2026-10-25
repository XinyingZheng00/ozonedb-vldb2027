# Campaign cost2 extraction

`extract_all.sh` turns the raw result files of the cost campaign into the
`results-cost2-*.tsv` tables. `plot_cost_model.py` reads these tables. The script
writes them to `bench/`. The artifact keeps them in `../../data/`.
`summarize.py` prints one line per cell. `zipf_check.py` checks the key
distribution of a run.

Set `OZONEDB_HOME` before you run any of these scripts. The shell chains that
drove the campaign are in `campaign-cost2-chains/`.
