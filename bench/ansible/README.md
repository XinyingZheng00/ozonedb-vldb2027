# Pushing code to the CloudLab nodes

Two playbooks. `bootstrap.yml` turns a freshly provisioned node into a working
one, straight from your local tree — no `git clone`, no GitHub credentials on the
node. `sync.yml` is the fast inner loop after that.

## One-time

```bash
pip install ansible                                    # bundles ansible.posix
# or: pip install ansible-core && ansible-galaxy collection install -r requirements.yml
```

## After a CloudLab swap

Update the `nodes:` block in `bench/scripts/config/ycsb.yaml` — every address in
the cluster lives there and nowhere else. Confirm it resolves, then go:

```bash
python3 ../scripts/ycsb_config.py --check   # print the resolved plan first

cd $OZONEDB_HOME/bench/ansible
ansible -m ping nodes                       # everything reachable?

ansible-playbook bootstrap.yml -e target=nodes   # provision the whole cluster
ansible-playbook bootstrap.yml                   # clients only (the default)
```

Each host's `setup.sh --role` comes from the group it landed in, so the one
`-e target=nodes` pass gives clients `--role client`, the log node
`--role corfu-server` and the store node `--role minio`.

`bootstrap.yml` pushes the working tree and then runs `bench/scripts/setup.sh`
with that host's role on each host (detached via `async`, since the first run
clones vcpkg, builds the C++ dependencies and, on the log node, builds
CorfuDB). Useful flags:

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

Run everything from this directory — Ansible only reads `ansible.cfg` from the
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

`disk_cache.yml` runs `bench/scripts/setup_disk_cache.sh` on every client to format and
mount the unused local SSD as the disk-cache tier at `/tank/cache` (ext4, label
`ozcache`); it is idempotent, so re-running is safe once a host is already mounted.

```bash
ansible-playbook sync.yml           # ship the script first
ansible-playbook disk_cache.yml -e device=/dev/sdb
```

## Where the hosts come from

`inventory.py` goes through `bench/scripts/ycsb_config.py`, the same loader the
benchmark runners use, so Ansible and `run_multinode_ycsb.py` cannot disagree
about which machines are in the experiment. After a CloudLab re-swap, edit
`nodes:` in `ycsb.yaml` and nothing else.

```bash
./inventory.py --list       # what Ansible sees
```

Groups: `clients`, `log`, `store`, `nodes` (all of them), and `cloudlab` as an
alias for `clients`. Playbooks default to `clients`; pass `-e target=<group>`
to aim elsewhere:

```bash
ansible-playbook bootstrap.yml -e target=nodes   # provision the whole cluster
ansible-playbook bootstrap.yml -e target=log     # just the CorfuDB node
```

`bootstrap.yml` picks each host's `setup.sh --role` from the group it is in, so
`-e target=nodes` gives clients `--role client`, the log node `--role
corfu-server` and the store node `--role minio` in one pass.

Every host also carries `lan_address` — the internal address other nodes use.
That is **not** the connection address; Ansible reaches hosts via
`ansible_host`. Use it when configuring what a service binds or dials.

## What gets excluded

Both playbooks share one list, in `group_vars/all.yml`. `rsync_excludes_base`
drops what is never useful on a node — `build/`, `vcpkg/`, `target/`,
`bench/results/`, `__pycache__`, compiled objects, editor scratch, and your
`ycsb.yaml` is pushed because the runners on the node read it. `sync.yml`
adds `.git/` (`rsync_excludes_incremental`).

The node needs no GitHub access: the engine tree arrives by rsync, and
`setup.sh` clones only the public vcpkg and CorfuDB repositories.

## Destination

Each host is probed for `OZONEDB_HOME` via a login shell (the same thing
`run_multinode_ycsb.py` does), and the tree lands there. Hosts that don't have
it set yet fall back to `~/ozonedb`. Override with
`-e remote_repo_path=/abs/path`, which also skips the probe.

## Notes

- Result files still come back through `run_multinode_ycsb.py`'s own `scp`
  pull. This playbook only handles the outbound direction.
- With many hosts on a slow uplink, the WAN transfer is the floor: the tree is
  uploaded once per host. Syncing to one node and having it fan out over
  CloudLab's internal network would beat this, but it needs the nodes to hold
  each other's keys.
