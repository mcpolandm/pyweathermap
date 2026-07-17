import time
import threading
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
    return {"status": "loading", "wmap": None, "png": None, "updated": None, "error": None, "lock": threading.Lock(), "last_viewed": time.time(),}

# Builds a fresh WeatherMap for this group and stores it in the map entry.
# Runs in a background thread.
def build(app, registry, group_id, switches, traffic_interval, notice_url, seconds=60, start_loop=True, evictable=False):
    entry = app.config["MAPS"][group_id]
    try:
        wmap = snmp_config.config_from_snmp(registry, switches, seconds)
        png = MapRenderer(wmap).render_to_bytes("PNG")
        with entry["lock"]:
            entry["wmap"] = wmap
            entry["png"] = png
            entry["png_filtered"] = MapRenderer(wmap.filtered(True)).render_to_bytes("PNG")
            entry["updated"] = time.time()
            entry["status"] = "ready"
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

# Returns the map entry for name's group, or making a build thread if new.
def get_or_create_map(app, registry, name, traffic_interval, startup):
    group_id, _, switches = resolve(registry, name)
    with app.config["MAPS_LOCK"]:
        entry = app.config["MAPS"].get(group_id)
        if entry is None:
            entry = new_map_entry()
            app.config["MAPS"][group_id] = entry
            threading.Thread(target=build, args=(app, registry, group_id, switches, traffic_interval, f"/map/{group_id}"), kwargs={"seconds": startup}, daemon=True).start()
    return group_id, entry

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
        notice_url = f"/map/{group_id}"
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
                    link.in_bps = (in2 - in1) * 8 // elapsed
                    link.out_bps = (out2 - out1) * 8 // elapsed
                # Render updated WeatherMap diagram and refresh update time
                entry["png"] = MapRenderer(wm).render_to_bytes("PNG")
                entry["png_filtered"] = MapRenderer(wm.filtered(True)).render_to_bytes("PNG")
                entry["updated"] = time.time()

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