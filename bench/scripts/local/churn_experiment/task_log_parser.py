#!/usr/bin/env python3
"""Parse OzoneDB task.log into a JSON timeline.

The task log is a sequence of varint32-length-prefixed protobuf TaskRecord
messages (see src/util/protobuf_serializer.cpp). This module decodes the file
into ordered records — the file offset of each record is preserved, since the
shared log's total order is defined by file offset (Section 4.1 of the paper).

Usage as a CLI:
    python3 task_log_parser.py <db_path>/task.log > timeline.json

record_pb2 (the protobuf bindings for record.proto) is generated on first use
into $OZONEDB_HOME/build/generated/python with protoc if it is not importable.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from google.protobuf.internal.decoder import _DecodeVarint32


def _require_env(name: str) -> Path:
    """Resolve a required environment variable to a Path, or exit clearly."""
    value = os.environ.get(name)
    if not value:
        raise SystemExit(f"{name} is not set")
    return Path(value)


_record_pb2 = None
# Filled by _load_record_pb2() on first use (needs the generated enum values).
_STATUS_NAMES: dict = {}


def _load_record_pb2():
    """Import record_pb2, generating it from record.proto with protoc on first use.

    Tries a plain `import record_pb2` first. On ImportError, runs
        protoc --proto_path=$OZONEDB_HOME/src/include/ozonedb \
               --python_out=$OZONEDB_HOME/build/generated/python record.proto
    (reusing an already generated file in that directory when present),
    adds the output directory to sys.path, and imports it.
    """
    global _record_pb2
    if _record_pb2 is not None:
        return _record_pb2
    try:
        import record_pb2  # type: ignore
    except ImportError:
        home = _require_env("OZONEDB_HOME")
        proto_dir = home / "src" / "include" / "ozonedb"
        out_dir = home / "build" / "generated" / "python"
        if not (out_dir / "record_pb2.py").exists():
            if shutil.which("protoc") is None:
                raise SystemExit(
                    "protoc is not on PATH; it is needed to generate record_pb2.py "
                    f"from {proto_dir / 'record.proto'} into {out_dir}"
                )
            out_dir.mkdir(parents=True, exist_ok=True)
            subprocess.run(
                ["protoc", f"--proto_path={proto_dir}",
                 f"--python_out={out_dir}", "record.proto"],
                check=True,
            )
        if str(out_dir) not in sys.path:
            sys.path.insert(0, str(out_dir))
        import record_pb2  # type: ignore
    _record_pb2 = record_pb2
    _STATUS_NAMES.update({
        record_pb2.TaskRecord.TASK_BEGIN: "BEGIN",
        record_pb2.TaskRecord.TASK_IN_PROGRESS: "IN_PROGRESS",
        record_pb2.TaskRecord.TASK_COMPLETE: "COMPLETE",
        record_pb2.TaskRecord.TASK_DEAD: "DEAD",
    })
    return record_pb2


def task_id_key(task_id: "record_pb2.TaskRecord.TaskIdentifier") -> str:
    """Stable string key for a TaskIdentifier (used to group records)."""
    return "|".join(task_id.input_files) + f"=>L{task_id.destinationLevel}"


def parse_task_log(path: Path) -> list[dict]:
    """Return ordered list of decoded records from task.log.

    Each entry has: file_offset, owner, owner_generation, status, task_key,
    input_files, destination_level.
    """
    record_pb2 = _load_record_pb2()
    data = path.read_bytes()
    pos = 0
    out: list[dict] = []
    record_index = 0
    while pos < len(data):
        try:
            msg_len, new_pos = _DecodeVarint32(data, pos)
        except IndexError:
            break
        if new_pos + msg_len > len(data):
            # truncated record — typically means a writer was killed mid-append.
            # Stop here; everything before this is intact thanks to atomic
            # append at the storage layer (a writer can't be killed *during* a
            # single append on a local FS — append() is one syscall).
            break
        rec = record_pb2.TaskRecord()
        rec.ParseFromString(data[new_pos:new_pos + msg_len])
        out.append({
            "record_index": record_index,
            "file_offset": pos,
            "owner": rec.owner,
            "owner_generation": rec.owner_generation,
            "status": _STATUS_NAMES.get(rec.status, str(rec.status)),
            "task_key": task_id_key(rec.task_id),
            "input_files": list(rec.task_id.input_files),
            "destination_level": rec.task_id.destinationLevel,
            "compact_into_next_level": rec.task_id.compactIntoNextLevel,
        })
        pos = new_pos + msg_len
        record_index += 1
    return out


def group_by_task(records: list[dict]) -> dict[str, list[dict]]:
    """Group decoded records by task_key, preserving append order."""
    groups: dict[str, list[dict]] = {}
    for r in records:
        groups.setdefault(r["task_key"], []).append(r)
    return groups


def task_summary(records_for_task: list[dict]) -> dict:
    """Summarise the lifecycle of one TaskID across all generations."""
    by_gen: dict[int, dict] = {}
    for r in records_for_task:
        g = by_gen.setdefault(r["owner_generation"], {
            "owner_generation": r["owner_generation"],
            "owners": [],
            "statuses_seen": [],
            "first_begin_record_index": None,
            "first_in_progress_record_index": None,
            "complete_record_index": None,
            "n_heartbeats": 0,
        })
        if r["owner"] not in g["owners"]:
            g["owners"].append(r["owner"])
        g["statuses_seen"].append(r["status"])
        if r["status"] == "BEGIN" and g["first_begin_record_index"] is None:
            g["first_begin_record_index"] = r["record_index"]
        if r["status"] == "IN_PROGRESS":
            g["n_heartbeats"] += 1
            if g["first_in_progress_record_index"] is None:
                g["first_in_progress_record_index"] = r["record_index"]
        if r["status"] == "COMPLETE" and g["complete_record_index"] is None:
            g["complete_record_index"] = r["record_index"]

    return {
        "task_key": records_for_task[0]["task_key"],
        "input_files": records_for_task[0]["input_files"],
        "destination_level": records_for_task[0]["destination_level"],
        "n_generations": len(by_gen),
        "max_generation": max(by_gen.keys()),
        "ever_completed": any(g["complete_record_index"] is not None
                              for g in by_gen.values()),
        "generations": [by_gen[g] for g in sorted(by_gen.keys())],
    }


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: task_log_parser.py <task.log>", file=sys.stderr)
        return 2
    path = Path(sys.argv[1])
    if not path.exists():
        print(f"missing: {path}", file=sys.stderr)
        return 1
    records = parse_task_log(path)
    grouped = group_by_task(records)
    summaries = [task_summary(grouped[k]) for k in grouped]
    json.dump({
        "n_records": len(records),
        "n_tasks": len(grouped),
        "n_completed_tasks": sum(1 for s in summaries if s["ever_completed"]),
        "n_with_reassignment": sum(1 for s in summaries if s["max_generation"] > 0),
        "records": records,
        "tasks": summaries,
    }, sys.stdout, indent=2)
    return 0


if __name__ == "__main__":
    sys.exit(main())
