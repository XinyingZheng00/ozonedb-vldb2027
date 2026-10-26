# Pushing code to the CloudLab nodes

## Bootstrap

Update the `nodes:` block in `bench/scripts/config/ycsb.yaml`.

```bash
python3 ../scripts/ycsb_config.py --check   # print the resolved node layout

cd $OZONEDB_HOME/bench/ansible
ansible -m ping nodes

ansible-playbook bootstrap.yml -e target=nodes   # provision the whole cluster
ansible-playbook bootstrap.yml                   # clients only (the default)
```

Each host's `setup.sh --role` comes from its group, so one
`-e target=nodes` pass gives clients `--role client`, the log node
`--role corfu-server` and the store node `--role minio`.

`bootstrap.yml` pushes the working tree, then runs `bench/scripts/setup.sh`
with that host's role on each host. The run is detached through `async`: the
first run clones vcpkg, builds the C++ dependencies and, on the log node,
builds CorfuDB. Flags:

```bash
-e run_setup=false                   # push only, don't provision
-e include_git=false                 # skip .git/ (40 of the 59 MB)
-e '{"setup_roles":["client"]}'      # override the role from the inventory
-e setup_extra_args="--jdk 17"       # anything else setup.sh takes
```

## Every push after that

```bash
ansible-playbook sync.yml                  # push changed files to every host
ansible-playbook sync.yml -e build=true    # push, then run build.sh on each host
ansible-playbook sync.yml --check --diff   # dry run (rsync -n); see what would move
ansible-playbook sync.yml --limit <host>
ansible-playbook sync.yml -e delete=true   # mirror: prune remote-only files too
```

## Payload

Both playbooks exclude `vcpkg/` and `build/`: the node clones vcpkg itself
(`setup.sh`) and builds there. `sync.yml` also excludes `.git/`.

Extra repos:

```bash
ansible-playbook sync.yml \
  -e '{"extra_repos":[{"src":"../../../slatedb/","dest":"slatedb"}]}'
```

## Disk cache

`disk_cache.yml` runs `bench/scripts/setup_disk_cache.sh` on every client. The
script formats and mounts the unused local SSD as the disk-cache tier at
`/tank/cache` (ext4, label `ozcache`). It is idempotent, so a re-run on a host
that is mounted is safe.

```bash
ansible-playbook sync.yml           # ship the script first
ansible-playbook disk_cache.yml -e device=/dev/sdb
```

## Where the hosts come from

`inventory.py` reads `bench/scripts/ycsb_config.py`, the loader the benchmark
runners use, so Ansible and `run_multinode_ycsb.py` see the same machines.
After a CloudLab re-swap, edit `nodes:` in `ycsb.yaml` and nothing else.

```bash
./inventory.py --list       # what Ansible sees
```

Groups: `clients`, `log`, `store`, `nodes`, and `cloudlab` as an
alias for `clients`. Playbooks default to `clients`; pass `-e target=<group>`
to select another group:

```bash
ansible-playbook bootstrap.yml -e target=nodes   # provision the whole cluster
ansible-playbook bootstrap.yml -e target=log     # just the CorfuDB node
```

`bootstrap.yml` takes each host's `setup.sh --role` from its group, so
`-e target=nodes` gives clients `--role client`, the log node `--role
corfu-server` and the store node `--role minio` in one pass.
