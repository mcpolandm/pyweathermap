import time
import threading
import gc
import pyweathermap.getting_traffic as datasource
from pyweathermap.renderer import MapRenderer
import pyweathermap.switch_registration as registration
import pyweathermap.config as snmp_config

# Resolves name/IP into (group_id, canonical_name, switches).
def resolve(registry, name):
    switches = registration.get_center_nodes(registry, name)
    return switches[0].group.lower(), switches[0].name.lower(), switches

# Resolves ip and community into (route, switch).
def resolve_ip(registry, ip, community):
    switch = registry.get(ip)
    if switch is not None:
        switch = switch._replace(community=community, group=ip, file="NONE")
    else:
        switch = registration.Switch(ip=ip, name=ip, community=community, file="NONE", group=ip)
    return f"get:{ip}:{community}", switch

# Helper function to set map entry.
def new_map_entry():
    return {
        "status": "loading", "wmap": None, "png": None, "updated": None, "error": None,
        "lock": threading.Lock(), "last_viewed": time.time(),
        "png_filtered": {}, "png_filtered_for": None,
    }

# Renders a WeatherMap (optionally filtered) to PNG bytes.
def _render_png(wmap, hide_non_switches=False, hide_non_lldp=False):
    view = wmap.filtered(hide_non_switches, hide_non_lldp, False) if (hide_non_switches or hide_non_lldp) else wmap
    return MapRenderer(view, show_labels=False).render_to_bytes("PNG")

# Gets filtered PNG for a ready map entry.
def get_filtered_png(entry, hide_non_switches, hide_non_lldp):
    key = (hide_non_switches, hide_non_lldp)
    with entry["lock"]:
        wmap, updated = entry["wmap"], entry["updated"]
        if entry["png_filtered_for"] == updated and key in entry["png_filtered"]:
            return entry["png_filtered"][key]

    png = _render_png(wmap, hide_non_switches, hide_non_lldp)

    with entry["lock"]:
        if entry["updated"] == updated:  # still current; don't clobber a newer render
            if entry["png_filtered_for"] != updated:
                entry["png_filtered"] = {}
                entry["png_filtered_for"] = updated
            entry["png_filtered"][key] = png
    gc.collect()
    return png

# Retrieves /map url for /get IP
def existing_map_url(app, registry, ip):
    switch = registry.get(ip)
    if switch is None:
        return None
    group_id = switch.group.lower()
    entry = app.config["MAPS"].get(group_id)
    if entry is None or entry["status"] != "ready":
        return None
    return f"/map/{switch.name.lower()}"


# Builds a fresh WeatherMap for this group and stores it in the map entry.
# Runs in a background thread.
def build(app, registry, group_id, switches, traffic_interval, notice_url, seconds=60, start_loop=True, evictable=False):
    entry = app.config["MAPS"][group_id]
    try:
        wmap = snmp_config.config_from_snmp(registry, switches, seconds)
        png = _render_png(wmap)
        # Pre-rendered since it's important enough to always be ready on request,
        # rather than paying the render cost on the first visitor to ask for it.
        png_hide_lldp = _render_png(wmap, hide_non_lldp=True)
        with entry["lock"]:
            entry["wmap"] = wmap
            entry["png"] = png
            entry["updated"] = time.time()
            entry["png_filtered"] = {(False, True): png_hide_lldp}
            entry["png_filtered_for"] = entry["updated"]
            entry["status"] = "ready"
        gc.collect()
        if start_loop:
            with app.config["NOTICES_LOCK"]:
                app.config["NOTICES"].append({
                    "name": switches[0].name,
                    "url": notice_url,
                    "ts": time.time(),
                    "type": "ready",
                })
            threading.Thread(target=traffic_update_loop, args=(app, registry, group_id, switches, notice_url, traffic_interval), kwargs={"evictable": evictable}, daemon=True).start()
    except Exception as exc:
        with entry["lock"]:
            entry["status"] = "error"
            entry["error"] = str(exc)
        with app.config["NOTICES_LOCK"]:
            app.config["NOTICES"].append({
                "name": switches[0].name,
                "url": notice_url,
                "ts": time.time(),
                "type": "error",
            })

def build_all(app, registry, traffic_interval, notice_url, seconds=60, start_loop=True, evictable=False):
    entry = app.config["MAPS"]["all"]
    try:
        switches = [switch for switch in registration.unique_switches(registry)]
        wmap = snmp_config.config_from_snmp(registry, switches, seconds)
        wmap = wmap.filtered(True, True, True)
        png = _render_png(wmap)
        # Pre-rendered since it's important enough to always be ready on request,
        # rather than paying the render cost on the first visitor to ask for it.
        png_hide_lldp = _render_png(wmap, hide_non_lldp=True)
        with entry["lock"]:
            entry["wmap"] = wmap
            entry["png"] = png
            entry["updated"] = time.time()
            entry["png_filtered"] = {(False, True): png_hide_lldp}
            entry["png_filtered_for"] = entry["updated"]
            entry["status"] = "ready"
        gc.collect()
        if start_loop:
            with app.config["NOTICES_LOCK"]:
                app.config["NOTICES"].append({
                    "name": "all",
                    "url": notice_url,
                    "ts": time.time(),
                    "type": "ready",
                })
            #threading.Thread(target=traffic_update_loop, args=(app, registry, group_id, switches, notice_url, traffic_interval), kwargs={"evictable": evictable}, daemon=True).start()
    except Exception as exc:
        with entry["lock"]:
            entry["status"] = "error"
            entry["error"] = str(exc)
        with app.config["NOTICES_LOCK"]:
            app.config["NOTICES"].append({
                "name": "all",
                "url": notice_url,
                "ts": time.time(),
                "type": "error",
            })

# Returns the map entry for name's group, or making a build thread if new.
def get_or_create_map(app, registry, name, traffic_interval, startup):
    group_id, _, switches = resolve(registry, name)
    with app.config["MAPS_LOCK"]:
        entry = app.config["MAPS"].get(group_id)
        if entry is None:
            entry = new_map_entry()
            app.config["MAPS"][group_id] = entry
            threading.Thread(target=build, args=(app, registry, group_id, switches, traffic_interval, f"/map/{switches[0].name.lower()}"), kwargs={"seconds": startup}, daemon=True).start()
    return group_id, entry

# Returns the map entry for the ip address, called from the get route.
def get_or_create_ip_map(app, registry, ip, community, traffic_interval, startup):
    group_id, switch = resolve_ip(registry, ip, community)
    with app.config["MAPS_LOCK"]:
        entry = app.config["MAPS"].get(group_id)
        if entry is None:
            entry = new_map_entry()
            app.config["MAPS"][group_id] = entry
            threading.Thread(target=build, args=(app, registry, group_id, [switch], traffic_interval, f"/get/{ip}/{community}"), kwargs={"seconds": startup, "evictable": True}, daemon=True).start()
        entry["last_viewed"] = time.time()
    return group_id, entry

def get_or_create_all(app, registry, traffic_interval, startup):
    with app.config["MAPS_LOCK"]:
        entry = app.config["MAPS"].get("all")
        if entry is None:
            entry = new_map_entry()
            app.config["MAPS"]["all"] = entry
            threading.Thread(target=build_all, args=(app, registry, traffic_interval, f"/map/all"), kwargs={"seconds": startup, "evictable": True}, daemon=True).start()
        entry["last_viewed"] = time.time()
    return entry

# Resets a failed map entry back to "loading" and starts a fresh build thread.
# No-op if the map isn't currently in "error" (e.g. already retried, or never failed).
def retry_map(app, registry, traffic_interval, startup, name=None, ip=None, community=None):
    if ip is not None:
        group_id, switch = resolve_ip(registry, ip, community)
        switches = [switch]
        notice_url = f"/get/{ip}/{community}"
        evictable = True
    else:
        group_id, _, switches = resolve(registry, name)
        notice_url = f"/map/{switches[0].name.lower()}"
        evictable = False

    with app.config["MAPS_LOCK"]:
        entry = app.config["MAPS"].get(group_id)
        if entry is None or entry["status"] != "error":
            return group_id
        entry["status"] = "loading"
        entry["error"] = None
        threading.Thread(target=build, args=(app, registry, group_id, switches, traffic_interval, notice_url), kwargs={"seconds": startup, "evictable": evictable}, daemon=True).start()
    return group_id

# Background process to update one map's rendered image every interval seconds
# with recent traffic data. One of these loops runs per built map (started once,
# right after that map's first successful build).
def traffic_update_loop(app, registry, group_id, switches, notice_url, interval=300, evictable=False):
    cycle = 0
    window_start = time.time()
    while True:
        entry = app.config["MAPS"].get(group_id)
        if entry is None:
            return
        with entry["lock"]:
            wm = entry["wmap"]

        # Sample twice, with interval seconds between
        sample1 = datasource.sample_all_links(wm)
        t1 = time.time()
        time.sleep(interval)
        sample2 = datasource.sample_all_links(wm)
        elapsed = time.time() - t1

        with entry["lock"]:
            if entry["wmap"] is wm:
                # Calculate new in_bps and out_bps values and save for each link in WeatherMap
                for name, (in2, out2) in sample2.items():
                    in1, out1 = sample1.get(name, (in2, out2))
                    link = wm.links.get(name)
                    if link is None:
                        continue
                    link.in_bps = max(0, (in2 - in1)) * 8 // elapsed
                    link.out_bps = max(0 ,(out2 - out1)) * 8 // elapsed
                # Render updated WeatherMap diagram and refresh update time.
                entry["png"] = _render_png(wm)
                # Keep the hide-non-LLDP PNG pre-rendered alongside the base one.
                entry["updated"] = time.time()
                entry["png_filtered"] = {(False, True): _render_png(wm, hide_non_lldp=True)}
                entry["png_filtered_for"] = entry["updated"]
        gc.collect()

        cycle += 1
        if cycle % 3 == 0:
            if evictable:
                with entry["lock"]:
                    last_viewed = entry["last_viewed"]
                if last_viewed < window_start:
                    with app.config["MAPS_LOCK"]:
                        if app.config["MAPS"].get(group_id) is entry:
                            del app.config["MAPS"][group_id]
                    return
            window_start = time.time()
            threading.Thread(target=build, args=(app, registry, group_id, switches, interval, notice_url), kwargs={"seconds": interval, "start_loop": False}, daemon=True).start()