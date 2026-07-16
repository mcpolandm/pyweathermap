import pyweathermap.getting_traffic as datasource
import pyweathermap.layout as layout
import pyweathermap.librenms_integration as libre
from concurrent.futures import ThreadPoolExecutor
from .models import (
    WeatherMap, MapNode, MapLink
)
# Helper function to determine if device is a switch but not a center switch
def match_registry_name(device_name, registry, switches):
    key = device_name.lower()
    if key in registry and key not in switches:
        return key
    short_key = key.split(".", 1)[0]
    if short_key in registry and short_key not in switches:
        return short_key
    return None


# Helper function to iterate through DataFrame for one switch and add all MapNode and MapLink objects to WeatherMap.
# Does not allow multiple MapNodes of same device name to allow connections to multiple switches.
def create_nodes_and_links(wm, df, switch, registry, switches):
    for _, row in df.iterrows():
        device_name = row["sysname"]
        matched = match_registry_name(device_name, registry, switches)
        if matched:
            device_name = registry[matched].name
        if device_name not in wm.nodes:
            wm.nodes[device_name] = MapNode(name=device_name, label=device_name)
            if matched:
                wm.nodes[device_name].infourl = f"/map/{matched}"
                wm.nodes[device_name].node_type = "endpoint/switch"
        # Link name must be unique, even with multiple links between same switch and node
        link_name = f"{switch.name}_{row['interface']}"
        link = MapLink(name=link_name, node1=switch.name, node2=device_name, bandwidth=row["Bandwidth"], in_bps=row["In Diff"], out_bps=row["Out Diff"], snmp_index=row['index'], iface1=row['interface'], iface2=datasource.clean_iface(row.get('remote interface')))
        wm.links[link_name] = link

# Primary function called by main.py to initialize WeatherMap object and collect startup data.
# Collects switch information from a local file, and threads execution of get_traffic for each switch.
# Builds WeatherMap from switch DataFrames and calls auto_layout to set Node positions.
def config_from_snmp(registry, switches, seconds=60):
    # Helper function to call get_traffic with remote hostname file if listed in the switches file
    def get_traffic_for_switch(sw):
        if sw.file != "NONE":
            return datasource.get_traffic(sw.ip, sw.community, seconds, sw.file)
        return datasource.get_traffic(sw.ip, sw.community, seconds)

    # Thread execution of get_traffic for each switch to keep data as accurate as possible
    with ThreadPoolExecutor() as pool:
        dataframes = list(pool.map(get_traffic_for_switch, switches))

    wm = WeatherMap(title=f"Network Map {switches[0].group}")

    # Add switch MapNode and call create_nodes_and_links to add information from DataFrame to WeatherMap
    for switch, df in zip(switches, dataframes):
        if not df.attrs.get("lldp_known", True):
            wm.no_lldp_switches.add(switch.name)
        infourl = libre.get_device_url(switch.ip)
        node = MapNode(name=switch.name, label=switch.name, node_type="switch", ip=switch.ip, community=switch.community, icon_type="rbox", icon_height=30, icon_width=60, infourl=infourl)
        wm.nodes[switch.name] = node
        create_nodes_and_links(wm, df, switch, registry, switches)

    # Call auto_layout to position MapNodes
    layout.auto_layout(wm)

    # Returns complete WeatherMap to main.py
    return wm
