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

# Builds a fresh WeatherMap for this group and stores it in the map entry.
# Runs in a background thread.
def build(app, registry, group_id, switches, traffic_interval, seconds=60, start_loop=True):
    entry = app.config["MAPS"][group_id]
    try:
        wmap = snmp_config.config_from_snmp(registry, switches, seconds)
        png = MapRenderer(wmap).render_to_bytes("PNG")
        with entry["lock"]:
            entry["wmap"] = wmap
            entry["png"] = png
            entry["updated"] = time.time()
            entry["status"] = "ready"
        if start_loop:
            with app.config["NOTICES_LOCK"]:
                app.config["NOTICES"].append({
                    "name": switches[0].name,
                    "url": f"/map/{group_id}",
                    "ts": time.time(),
                    "type": "ready",
                })
            threading.Thread(target=traffic_update_loop, args=(app, registry, group_id, switches, traffic_interval), daemon=True).start()
    except Exception as exc:
        with entry["lock"]:
            entry["status"] = "error"
            entry["error"] = str(exc)
        with app.config["NOTICES_LOCK"]:
            app.config["NOTICES"].append({
                "name": switches[0].name,
                "url": f"/map/{group_id}",
                "ts": time.time(),
                "type": "error",
            })

# Returns the map entry for name's group, or making a build thread if new.
def get_or_create_map(app, registry, name, traffic_interval, startup):
    group_id, _, switches = resolve(registry, name)
    with app.config["MAPS_LOCK"]:
        entry = app.config["MAPS"].get(group_id)
        if entry is None:
            entry = {
                "status": "loading",
                "wmap": None,
                "png": None,
                "updated": None,
                "error": None,
                "lock": threading.Lock(),
            }
            app.config["MAPS"][group_id] = entry
            threading.Thread(target=build, args=(app, registry, group_id, switches, traffic_interval), kwargs={"seconds": startup}, daemon=True).start()
    return group_id, entry

# Resets a failed map entry back to "loading" and starts a fresh build thread.
# No-op if the map isn't currently in "error" (e.g. already retried, or never failed).
def retry_map(app, registry, name, traffic_interval, startup):
    group_id, _, switches = resolve(registry, name)
    with app.config["MAPS_LOCK"]:
        entry = app.config["MAPS"].get(group_id)
        if entry is None or entry["status"] != "error":
            return group_id
        entry["status"] = "loading"
        entry["error"] = None
        threading.Thread(target=build, args=(app, registry, group_id, switches, traffic_interval), kwargs={"seconds": startup}, daemon=True).start()
    return group_id

# Background process to update one map's rendered image every interval seconds
# with recent traffic data. One of these loops runs per built map (started once,
# right after that map's first successful build).
def traffic_update_loop(app, registry, group_id, switches, interval=300):
    cycle = 0
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
                entry["updated"] = time.time()

        cycle += 1
        if cycle % 3 == 0:
            threading.Thread(target=build, args=(app, registry, group_id, switches, interval), kwargs={"seconds": interval, "start_loop": False}, daemon=True).start()