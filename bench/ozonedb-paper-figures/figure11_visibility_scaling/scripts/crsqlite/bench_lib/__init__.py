"""Shared machinery for the cr-sqlite YCSB benchmark.

The public contract is the result-file naming and the [AGGREGATE]/[OP] metric
arithmetic in results.py. It is kept byte-compatible with
ozonedb/bench/scripts/local/load_local_ycsb_multiproc.py, so the same scripts
can plot the results of the two engines.
"""
