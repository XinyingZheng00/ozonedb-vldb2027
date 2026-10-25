"""Build a per-trial OzoneDB shared_config JSON. It uses the same defaults as the
paper's own multi-process YCSB experiment (src/config/local/shared_config_rocksdb_base.json):
mode=1 (MultipleProcesses), compaction_policy=0 (kHoAl), cloud=0."""
import json
from pathlib import Path

BASE_CONFIG = {
    "task_prefix": "task",
    "mode": 1,
    "log_file_size_limit": 33554432,
    "base_file_number_limit": 2,
    "level_size": "[268435456, 2684354560,26843545600,268435456000,2684354560000,26843545600000,268435456000000]",
    "level_file_size_limit": "[67108864, 67108864,67108864,67108864,67108864,67108864,67108864]",
    "max_level": 7,
    "compaction_policy": 0,
    "cloud": 0,
}


def write_config(db_path, config_out_path):
    cfg = dict(BASE_CONFIG)
    db_path = str(db_path)
    if not db_path.endswith("/"):
        db_path += "/"
    cfg["db_path"] = db_path
    Path(db_path).mkdir(parents=True, exist_ok=True)
    Path(config_out_path).write_text(json.dumps(cfg, indent=2))
    return config_out_path
