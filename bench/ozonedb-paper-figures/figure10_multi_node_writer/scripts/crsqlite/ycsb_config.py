#!/usr/bin/env python3
"""The one loader for bench/scripts/config/ycsb.yaml.

`nodes:` is the single source of truth for every address in the cluster. Each
client replica is named once, with two addresses. The two addresses are NOT
interchangeable:

    ssh   how the orchestrator reaches it. This is a public CloudLab name.
    lan   how other nodes reach it. This is the internal 10.10.1.x address
          that the sync daemons dial in multi-node runs.

This module computes the derived values exactly once, and writes them back
into the config dict under the keys that the consumers read:

    cloudlab.hosts                 = [c.ssh for c in nodes.clients]
    cloudlab.lan_hosts             = [c.lan (fallback ssh) for c in nodes.clients]
    cloudlab.ssh_user              = nodes.ssh_user
    cloudlab.ssh_private_key_path  = nodes.ssh_private_key_path

The module is also usable from the shell:

    python3 ycsb_config.py --get sync.port_base
    python3 ycsb_config.py --check                 # validate and print the plan
    python3 ycsb_config.py --json
"""

import argparse
import json
import os
import sys

import yaml

_HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_CONFIG_PATH = os.path.join(_HERE, "config", "ycsb.yaml")

# cr-sqlite has no server nodes. Every machine is a client replica. This is
# kept for interface parity with the ozonedb loader. inventory.py imports it.
SINGLETON_ROLES = ()


class ConfigError(ValueError):
    """The loader raises this for a config that would produce a silently wrong benchmark."""


def _as_node(entry, where):
    """Accept either `- host.example` or `- {ssh: host.example, lan: 10.0.0.1}`."""
    if isinstance(entry, str):
        return {"ssh": entry.strip()}
    if isinstance(entry, dict):
        return entry
    raise ConfigError(f"{where}: expected a hostname or a mapping, got {entry!r}")


def ssh_addr(node, where):
    """Return the address to SSH to. It falls back to lan. That is right when
    you orchestrate from inside the CloudLab LAN."""
    addr = (node.get("ssh") or node.get("lan") or "").strip()
    if not addr:
        raise ConfigError(f"{where}: needs an `ssh:` or `lan:` address")
    return addr


def lan_addr(node, where):
    """Return the address that other nodes use. The sync daemons dial this. It falls back to ssh."""
    addr = (node.get("lan") or node.get("ssh") or "").strip()
    if not addr:
        raise ConfigError(f"{where}: needs a `lan:` or `ssh:` address")
    return addr


def _reject_derived(cfg, dotted):
    """A hand-written value that `nodes:` also computes is the drift that this
    module exists to prevent. Refuse it rather than silently pick one."""
    section, _, key = dotted.partition(".")
    if isinstance(cfg.get(section), dict) and key in cfg[section]:
        raise ConfigError(
            f"{dotted} is derived from nodes.clients and must not be set by hand. Delete it."
        )


def derive(cfg):
    """Compute the derived addresses and write them into the config. Do nothing
    for a config without `nodes:`."""
    nodes = cfg.get("nodes")
    if not nodes:
        return cfg

    for dotted in ("cloudlab.hosts", "cloudlab.lan_hosts"):
        _reject_derived(cfg, dotted)

    clients = [
        _as_node(e, f"nodes.clients[{i}]")
        for i, e in enumerate(nodes.get("clients") or [])
    ]
    if not clients:
        raise ConfigError("nodes.clients is empty -- there is nothing to run YCSB on.")

    ssh_user = nodes.get("ssh_user")
    if not ssh_user:
        raise ConfigError("nodes.ssh_user is not set.")

    cfg["cloudlab"] = {
        "ssh_user": ssh_user,
        "ssh_private_key_path": nodes.get("ssh_private_key_path") or "~/.ssh/id_rsa",
        "hosts": [
            ssh_addr(c, f"nodes.clients[{i}]") for i, c in enumerate(clients)
        ],
        "lan_hosts": [
            lan_addr(c, f"nodes.clients[{i}]") for i, c in enumerate(clients)
        ],
    }
    return cfg


def load(path=None):
    path = path or os.environ.get("CRSQLITE_YCSB_CONFIG") or DEFAULT_CONFIG_PATH
    with open(path, "r") as f:
        cfg = yaml.safe_load(f) or {}
    return derive(cfg)


def get(cfg, dotted):
    cur = cfg
    for part in dotted.split("."):
        if isinstance(cur, list):
            cur = cur[int(part)]
        elif isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            raise ConfigError(f"no such config key: {dotted}")
    return cur


def crsqlite_extension_path(cfg):
    """Return the loadable-extension path. It is crsqlite.extension_path if set,
    else $CRSQLITE_HOME/lib/crsqlite. load_extension appends the platform suffix."""
    ext = ((cfg.get("crsqlite") or {}).get("extension_path") or "").strip()
    if ext:
        return ext
    home = os.environ.get("CRSQLITE_HOME")
    if not home:
        raise ConfigError(
            "crsqlite.extension_path is empty and CRSQLITE_HOME is not set."
        )
    return os.path.join(home, "lib", "crsqlite")


def _plan(cfg):
    out = []
    nodes = cfg.get("nodes") or {}
    for i, c in enumerate(nodes.get("clients") or []):
        n = _as_node(c, f"nodes.clients[{i}]")
        out.append(
            f"{'client'+str(i):<8} ssh={ssh_addr(n, 'c'):<28} lan={n.get('lan', '-')}"
        )
    out.append("")
    out.append(f"cloudlab.hosts      = {get(cfg, 'cloudlab.hosts')}")
    out.append(f"cloudlab.lan_hosts  = {get(cfg, 'cloudlab.lan_hosts')}   (sync daemons dial these)")
    sync = cfg.get("sync") or {}
    out.append(
        f"sync                = enabled={sync.get('enabled', True)} "
        f"interval_ms={sync.get('interval_ms', 100)} port_base={sync.get('port_base', 7801)}"
    )
    try:
        out.append(f"crsqlite.extension  = {crsqlite_extension_path(cfg)}")
    except ConfigError as e:
        out.append(f"crsqlite.extension  = UNRESOLVED ({e})")
    return "\n".join(out)


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--config", default=None)
    p.add_argument("--get", metavar="DOTTED", help="print one value, e.g. sync.port_base")
    p.add_argument("--check", action="store_true", help="validate and print the resolved plan")
    p.add_argument("--json", action="store_true", help="dump the fully resolved config")
    args = p.parse_args()

    try:
        cfg = load(args.config)
        if args.get:
            v = get(cfg, args.get)
            print(json.dumps(v) if isinstance(v, (list, dict)) else v)
        elif args.json:
            print(json.dumps(cfg, indent=2, default=str))
        else:
            print(_plan(cfg))
    except (ConfigError, OSError) as e:
        sys.stderr.write(f"ycsb_config: {e}\n")
        sys.exit(1)


if __name__ == "__main__":
    main()
