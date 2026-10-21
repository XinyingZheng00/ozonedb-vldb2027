# Campaign cost2 extraction

`extract_all.sh` turns the raw result files of the cost campaign into the
`bench/results-cost2-*.tsv` tables that `plot_cost_model.py` reads, and
`summarize.py` prints one line per cell. `zipf_check.py` checks the key
distribution of a run. Set `OZONEDB_HOME` first. The shell chains that
drove the campaign are in the artifact repository under `notebook/`.
