# Experiment cost-20260828 sweep scripts

These scripts run the cells of the cost experiment. Each script reads OZONEDB_HOME, the
node logins and the node hostnames from the environment. The header of each script lists
the variables. Each script writes its log to bench/results/local/cost-sweeps/.

sweep_oz100.sh takes the load writer count (16). summarize.py prints one line per cell
from the extractor. extract_all.sh rebuilds every TSV from the raw result directories.
