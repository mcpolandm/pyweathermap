import os
from collections import namedtuple
Switch = namedtuple("Switch", ["ip", "name", "community", "file", "group"])

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

def get_group_members(registry, group):
    unique_switches = dict.fromkeys(registry.values())
    return [switch for switch in unique_switches if switch.group == group]

def get_all_switches(registry):
    unique_switches = dict.fromkeys(registry.values())
    return sorted({(switch.name, switch.ip) for switch in unique_switches})

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
        defaults = [registry[k] for k in default_names]
        return defaults

    center_nodes = [registry[center_text.lower()]]
    if center_nodes[0].group == center_nodes[0].name:
        return center_nodes

    return get_group_members(registry, center_nodes[0].group)