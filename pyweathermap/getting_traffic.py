import subprocess
import re
import pandas as pd
import time
import numpy as np
from concurrent.futures import ThreadPoolExecutor
from .models import (
    WeatherMap
)


# Raised by get_traffic when a switch's SNMP data can't be turned into usable
# rows, so callers see what went wrong instead of a bare pandas KeyError.
class SwitchDataError(Exception):
    pass

# IF-MIB table roots polled via snmpbulkwalk.
_OID_IN = ".1.3.6.1.2.1.31.1.1.1.6"      # ifHCInOctets
_OID_OUT = ".1.3.6.1.2.1.31.1.1.1.10"    # ifHCOutOctets
_OID_SPEED = ".1.3.6.1.2.1.31.1.1.1.15"  # ifHighSpeed
_RE_COUNTER = r"\.(?:[0-9]+\.)+([0-9]+) = (?:Counter64|Gauge32): ([0-9]+)"

# Bulk-walks a whole IF-MIB table in one snmpbulkwalk call.
# Returns {ifIndex: value} for every interface.
def snmp_bulk_table(ip, community, oid):
    output = subprocess.run(["snmpbulkwalk", '-On', "-v2c", "-c", community, ip, oid], capture_output=True, text=True).stdout
    table = {}
    for line in output.strip().split("\n"):
        match = re.match(_RE_COUNTER, line.strip())
        if match:
            table[match.group(1)] = match.group(2)
    return table

# Called only by server.py when making traffic data updates.
# Groups links by switch and bulkwalks each switch's whole in/out tables once.
# Threads per switch so switches are still sampled in parallel.
def sample_all_links(wm: WeatherMap):
    by_switch = {}  # switch_name -> [(link_name, snmp_index), ...]
    for name, link in wm.links.items():
        switch = wm.nodes.get(link.node1)
        if link.snmp_index is None or switch is None or switch.ip is None:
            continue
        by_switch.setdefault(switch.name, []).append((name, link.snmp_index))

    # calls bulk walk, returns Dict of (name, in_bps, out_bps) for all links on switch.
    def fetch_switch(switch_name):
        switch = wm.nodes[switch_name]
        links = by_switch[switch_name]
        in_table = snmp_bulk_table(switch.ip, switch.community, _OID_IN)
        out_table = snmp_bulk_table(switch.ip, switch.community, _OID_OUT)
        return {
            name: (np.uint64(in_table[index]), np.uint64(out_table[index]))
            for name, index in links
            if index in in_table and index in out_table
        }

    with ThreadPoolExecutor(max_workers=len(by_switch) or 1) as pool:
        results = pool.map(fetch_switch, by_switch.keys())

    # Combine per-switch data into Dict with all switches.
    combined = {}
    for result in results:
        combined.update(result)
    return combined


# Collects LLDP port IDs to match to IF-MIB port IDs on common interface name.
# Uses LLDP port IDs to collect remote hostnames to save as Node names.
def get_lldp_neighbors(ip, community, df):
    # Collecting and matching interface names to get LLDP port IDs
    regex_loc_port = r'\.1\.0\.8802\.1\.1\.2\.1\.3\.7\.1\.3\.([0-9]*) = STRING: "?([/\(\)A-Za-z0-9-\.:]*)"?'
    output_remote = subprocess.run(['snmpbulkwalk', '-On', '-v2c', '-c', community, ip, ".1.0.8802.1.1.2.1.3.7.1.3"], capture_output=True, text=True).stdout
    for line in output_remote.strip().split("\n"):
        match = re.match(regex_loc_port, line.strip())
        if match:
            df.loc[df['interface'] == match.group(2), "LLDP Port"] = match.group(1)

    # Collecting remote hostnames to match to LLDP port IDs
    regex_remote_hostname = r'\.1\.0\.8802\.1\.1\.2\.1\.4\.1\.1\.9\.[0-9]*\.([0-9]*)\.[0-9]* = STRING: "?([\(\)A-Za-z0-9-\.:]*)"?'
    output_remote = subprocess.run(['snmpbulkwalk', '-On', '-v2c', '-c', community, ip, ".1.0.8802.1.1.2.1.4.1.1.9"], capture_output=True, text=True).stdout
    for line in output_remote.strip().split("\n"):
        match = re.match(regex_remote_hostname, line.strip())
        if match:
            df.loc[df["LLDP Port"] == match.group(1), "sysname"] = match.group(2)

    # Collecting remote port IDs (lldpRemPortId) to match to LLDP local port IDs.
    # Indexed the same way as lldpRemSysName above (timeMark.localPortNum.remIndex),
    # so it joins on the same "LLDP Port" column.
    regex_remote_port = r'\.1\.0\.8802\.1\.1\.2\.1\.4\.1\.1\.7\.[0-9]*\.([0-9]*)\.[0-9]* = STRING: "?([/\(\)A-Za-z0-9-\.:]*)"?'
    output_remote = subprocess.run(['snmpbulkwalk', '-On', '-v2c', '-c', community, ip, ".1.0.8802.1.1.2.1.4.1.1.7"], capture_output=True, text=True).stdout
    for line in output_remote.strip().split("\n"):
        match = re.match(regex_remote_port, line.strip())
        if match:
            df.loc[df["LLDP Port"] == match.group(1), "remote interface"] = match.group(2)


# Primary function called by config.py to collect initial data on the connections of a given switch.
# Collects IF-MIB index and interfaces to match to remote hostnames, bandwidth, and in/out traffic.
# Computes traffic by waiting for seconds between snmpget commands, then calculating difference.
def get_traffic(ip, community, seconds=300, interfaces=None):
    # Collect IF-MIB index and interfaces for future snmp commands
    regex_descr = r"\.1\.3\.6\.1\.2\.1\.2\.2\.1\.2\.([0-9]*) = STRING: \"?([A-Za-z0-9/\.:_-]*)\"?"
    output = subprocess.run(["snmpbulkwalk", '-On', "-v2c", "-c", community, ip, ".1.3.6.1.2.1.2.2.1.2"], capture_output=True, text=True).stdout
    temp = []
    for line in output.strip().split("\n"):
        match = re.match(regex_descr, line.strip())
        if match:
            temp.append({
                "index": match.group(1),
                "interface": match.group(2)
            })

    # Stores connection data in Pandas DataFrame for easy processing in the function
    df = pd.DataFrame(temp)

    # "interface" is only missing here when the ifDescr bulkwalk above returned no
    # rows at all, which almost always means SNMP itself failed against this switch.
    if "interface" not in df.columns:
        raise SwitchDataError(
            f"No interfaces returned by SNMP walk of ifDescr ({ip}, community={community!r}). "
            f"Raw snmpbulkwalk output: {output.strip()!r}. "
            "Check that the switch is reachable, the community string is correct, "
            "and that IF-MIB is supported/enabled on the device."
        )

    # If file of interfaces if provided, gets remote hostnames from there.
    # Used when LLDP is not enabled.
    if interfaces is not None:
        df_csv = pd.read_csv(interfaces)
        if "sysname" not in df_csv.columns:
            raise SwitchDataError(
                f"Interfaces file {interfaces!r} for switch {ip} is missing a 'sysname' column. "
                f"Columns found: {list(df_csv.columns)}."
            )
        merged = pd.merge(df, df_csv, on='interface', how='inner')
        if merged.empty:
            raise SwitchDataError(
                f"No interfaces from {ip} matched any row in {interfaces!r} on the 'interface' column. "
                f"Interfaces from SNMP: {sorted(df['interface'])}. "
                f"Interfaces in file: {sorted(df_csv['interface'])}."
            )
        df = merged
    else:
        output_remote = subprocess.run(['snmpbulkwalk', '-On', '-v2c', '-c', community, ip, ".1.0.8802.1.1.2.1.4.1.1.9"], capture_output=True, text=True).stdout
        if len(output_remote) == 0 or "at this OID" in output_remote:
            df["sysname"] = df["interface"]
        else:
            get_lldp_neighbors(ip, community, df)
            if "sysname" not in df.columns or df["sysname"].isna().all():
                raise SwitchDataError(
                    f"LLDP walk on {ip} returned data but no remote sysnames could be matched "
                    "to any local interface. This usually means the neighbor's LLDP MIB layout "
                    "doesn't match the expected format, or LLDP is enabled but no neighbors are up. "
                    f"Local interfaces seen: {sorted(df['interface'])}. "
                    f"Columns collected so far: {list(df.columns)}."
                )
            df = df.dropna(subset=["sysname"])
    
    bw_table = snmp_bulk_table(ip, community, _OID_SPEED)
    df['Bandwidth'] = df['index'].map(bw_table)
    df = df[df['Bandwidth'] != "0"]
    # Collects initial traffic values, waits seconds, collects second values
    in_table_init = snmp_bulk_table(ip, community, _OID_IN)
    out_table_init = snmp_bulk_table(ip, community, _OID_OUT)
    df['In Traffic Init'] = df['index'].map(in_table_init)
    df['Out Traffic Init'] = df['index'].map(out_table_init)

    print("Waiting to track live traffic")
    time.sleep(seconds)
    in_table_later = snmp_bulk_table(ip, community, _OID_IN)
    out_table_later = snmp_bulk_table(ip, community, _OID_OUT)
    df['In Traffic Later'] = df['index'].map(in_table_later)
    df['Out Traffic Later'] = df['index'].map(out_table_later)
    
    counter_cols = ['In Traffic Init', 'Out Traffic Init', 'In Traffic Later', 'Out Traffic Later']
    incomplete = df[df[counter_cols].isna().any(axis=1)]
    if not incomplete.empty:
        print(f"Dropping interfaces with no HC counters: {list(zip(incomplete['index'], incomplete['interface']))}", flush=True)
        df = df.dropna(subset=counter_cols)

    # Computes difference between intial and secondary traffic values to use for percentage use calculation in rendering.
    df = df.astype({'Bandwidth': 'uint64', 'In Traffic Init': 'uint64', 'Out Traffic Init': 'uint64', 'In Traffic Later': 'uint64', 'Out Traffic Later': 'uint64'})
    df['In Diff'] = df['In Traffic Later'] - df['In Traffic Init']
    df['Out Diff'] = df['Out Traffic Later'] - df['Out Traffic Init']
    df = df.drop(columns=['In Traffic Init', 'Out Traffic Init', 'In Traffic Later', 'Out Traffic Later'])

    # Convert all fields to bps
    df['Bandwidth'] = df['Bandwidth'] * 1000000
    df['In Diff'] = (df['In Diff'] * 8)//seconds
    df['Out Diff'] = (df['Out Diff'] * 8)//seconds

    return df


# Returns None for NaN values
def clean_iface(value):
    return None if value != value else value


if __name__ == "__main__":
    get_traffic()