<!--
Copyright (c) 2012 - 2016 YCSB contributors. All rights reserved.

Licensed under the Apache License, Version 2.0 (the "License"); you
may not use this file except in compliance with the License. You
may obtain a copy of the License at

http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or
implied. See the License for the specific language governing
permissions and limitations under the License. See accompanying
LICENSE file.
-->

## Quick Start

```
bash ${OZONEDB_HOME}/bench/scripts/build.sh
./bin/ycsb load ozonedb -s -P workloads/workloada (-p property)
```

`build.sh` produces `libOzoneDB.so` and `libozonedb.so` in `${OZONEDB_HOME}/build` and packages
this binding. There is nothing to install: `jni.OzoneDBJNI` (a source file in this module) loads
the shim from `$OZONEDB_HOME/build`, and the shim's `$ORIGIN` rpath finds `libOzoneDB.so` beside
it. Override the path with `-Dozonedb.native.lib=/abs/path/libozonedb.so` if you need to.

## Running YCSB against the CorfuDB backend

The CorfuDB backend reuses this same binding. The Java side is unchanged.
Point `shared_config` at a config file whose `"backend"` field is `"corfu"`.
The engine reaches the server with its own C++ client, so the YCSB JVM needs
nothing extra on its classpath.

### Prerequisites

1. Build OzoneKV with the Corfu backend enabled (`bench/scripts/build.sh`
   does this):
   ```
   cmake -B build -DOZONEDB_ENABLE_CORFU=ON
   cmake --build build
   ```
2. Start a Corfu server somewhere reachable (`bench/scripts/setup.sh --role
   corfu-server` installs one):
   ```
   corfu_server -l /mnt/corfu/run_batch -s -a <lan-address> 9090
   ```

### Via the bench scripts (recommended)

`db_name: ozonedb-corfu` in `bench/scripts/config/ycsb.yaml` makes the
multiprocess runners generate one config per writer from
`src/config/corfu/shared_config_base.json`:

```
python3 bench/scripts/local/load_local_ycsb_multiproc.py
python3 bench/scripts/local/run_local_ycsb_multiproc.py
```

### Manual invocation

```
cd ${OZONEDB_HOME}/ycsb
./bin/ycsb load ozonedb -s -P workloads/workloada \
    -p shared_config=/path/to/shared_config.json
```

Edit the config JSON beforehand so `corfu_endpoint`, `db_path` and the S3
settings match your environment. Never point two writer processes at one
config file. Each process needs its own `db_path`.

### Troubleshooting

- Handshake failures / connection refused: the endpoint must be the address
  that `corfu_server -a` was given. Use `127.0.0.1:9090`, not `localhost`.
- `[corfu] bootstrap` throws about a checkpoint ahead of the log: the Corfu
  log directory and the SSTable bucket are one unit. Restore them together.
