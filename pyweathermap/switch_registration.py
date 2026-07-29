import os
import ipaddress
import re
from collections import namedtuple
Switch = namedtuple("Switch", ["ip", "name", "community", "file", "group"])

# Checks that target is a valid IP address or hostname.
def is_valid_target(ip_or_host):
    try:
        ipaddress.ip_address(ip_or_host)
        return True
    except ValueError:
        pass
    return bool(re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?(?:\.[A-Za-z0-9-]{1,63})*", ip_or_host))

# Creates registry object from switch list file
def load_switch_registry(path):
    # Get switch information from file
    switches = {}
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split()
            if len(parts) == 4:
                ip, name, community, file = parts
                group = name
            elif len(parts) == 5:
                ip, name, community, file, group = parts
            else:
                raise ValueError(f"Malformed switch_list.txt line: {line!r}")
            switch = Switch(ip, name, community, file, group)
            switches[switch.name.lower()] = switch
            switches[switch.ip] = switch

    return switches

# Returns a list of unique switches in registry.
# Registry purposefully duplicates switches, keyed under both name and IP.
def unique_switches(registry):
    return dict.fromkeys(registry.values())

# Returns list of switches in a given group
def get_group_members(registry, group):
    return [switch for switch in unique_switches(registry) if switch.group == group]

# Returns list of all groups, with a representative from each
def get_named_groups(registry):
    groups = {}
    for switch in unique_switches(registry):
        groups.setdefault(switch.group, []).append(switch)
    named_groups = [(group, min(members, key=lambda s: s.name.lower()).name) for group, members in groups.items() if len(members) > 1]
    return sorted(named_groups, key=lambda pair: pair[0].lower())

# Returns all switch name and IP pairs in the registry
def get_all_switches(registry):
    return sorted({(switch.name, switch.ip) for switch in unique_switches(registry)}, key=lambda pair: pair[0].lower())

# Returns all center nodes for a group based on group or individual switch name.
def get_center_nodes(registry, center_text=None):
    if center_text is None or center_text.lower() not in registry:
        default_env = os.environ.get("PYWEATHERMAP_DEFAULT_CENTER")
        if not default_env:
            raise RuntimeError(
                "No --center given and PYWEATHERMAP_DEFAULT_CENTER is not set. "
                "Set it to a comma-separated list of default switch names/IPs, e.g. "
                "'export PYWEATHERMAP_DEFAULT_CENTER=switch1,switch2'"
            )
        default_names = [name.strip().lower() for name in default_env.split(",")]
        missing = [n for n in default_names if n not in registry]
        if missing:
            raise RuntimeError(
                f"PYWEATHERMAP_DEFAULT_CENTER references unknown switch(es) {missing!r}; "
                "check spelling against switch_list.txt."
            )
        defaults = [registry[k] for k in default_names]
        return defaults

    center_nodes = [registry[center_text.lower()]]
    if center_nodes[0].group == center_nodes[0].name:
        return center_nodes

    return get_group_members(registry, center_nodes[0].group)