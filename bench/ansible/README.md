# Pushing code to the CloudLab nodes

Two playbooks. `bootstrap.yml` turns a freshly provisioned node into a working
one, straight from your local tree: no `git clone` and no GitHub credentials on
the node. `sync.yml` is the incremental push after that.

## One-time

```bash
pip install ansible                                    # bundles ansible.posix
# or: pip install ansible-core && ansible-galaxy collection install -r requirements.yml
```

## After a CloudLab swap

Update the `nodes:` block in `bench/scripts/config/ycsb.yaml`. Every address in
the cluster lives there and nowhere else. Confirm that it resolves, then push:

```bash
python3 ../scripts/ycsb_config.py --check   # print the resolved node layout

cd $OZONEDB_HOME/bench/ansible
ansible -m ping nodes                       # everything reachable?

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

Run everything from this directory. Ansible reads `ansible.cfg` only from the
cwd.

## Payload

Both playbooks exclude `vcpkg/` and `build/`: the node clones vcpkg itself
(`setup.sh`) and builds there. `sync.yml` also excludes `.git/`. A push is a
few MB.

Sibling repos ride along:

```bash
ansible-playbook sync.yml \
  -e '{"extra_repos":[{"src":"../../../slatedb/","dest":"slatedb"}]}'
```

`src` is relative to this directory (or absolute); `dest` is relative to the
remote user's home (or absolute).

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

Groups: `clients`, `log`, `store`, `nodes` (all of them), and `cloudlab` as an
alias for `clients`. Playbooks default to `clients`; pass `-e target=<group>`
to select another group:

```bash
ansible-playbook bootstrap.yml -e target=nodes   # provision the whole cluster
ansible-playbook bootstrap.yml -e target=log     # just the CorfuDB node
```

`bootstrap.yml` takes each host's `setup.sh --role` from its group, so
`-e target=nodes` gives clients `--role client`, the log node `--role
corfu-server` and the store node `--role minio` in one pass.

Every host also carries `lan_address`, the internal address other nodes use.
That is **not** the connection address; Ansible reaches hosts through
`ansible_host`. Use it to set what a service binds or dials.

## What gets excluded

Both playbooks share one list, in `group_vars/all.yml`. `rsync_excludes_base`
drops what is never useful on a node: `build/`, `vcpkg/`, `target/`,
`bench/results/`, `__pycache__`, compiled objects and editor scratch. Your
`ycsb.yaml` is pushed because the runners on the node read it. `sync.yml`
adds `.git/` (`rsync_excludes_incremental`).

The node needs no GitHub access: the engine tree arrives by rsync, and
`setup.sh` clones only the public vcpkg and CorfuDB repositories.

## Destination

Each host is probed for `OZONEDB_HOME` through a login shell, as
`run_multinode_ycsb.py` does, and the tree lands there. A host without it
falls back to `~/ozonedb`. `-e remote_repo_path=/abs/path` overrides the
destination and skips the probe.

## Notes

- Result files come back through the `scp` pull of `run_multinode_ycsb.py`.
  The playbooks handle only the outbound direction.
- The tree is uploaded once per host, so with many hosts the WAN uplink bounds
  the push time.
