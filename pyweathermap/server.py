"""
Flask web server that serves weathermaps as live webpages.

Each switch/group gets its own map, built lazily on first visit and cached
by group name (so every member of a group, and every alias by which it can
be reached, shares one map instead of building duplicates).

Routes:
  GET /                        Redirects to the default switch's map
  GET /map/<name>               HTML page for the map centered on <name>'s group
                                 (shows a loading page while the map is being built)
  GET /map/<name>/map.png       Current map image for <name>'s group
"""

import threading
import time
from datetime import datetime
from flask import Flask, Response, render_template, redirect, abort, request

from .renderer import MapRenderer
import pyweathermap.map_server_manager as manager
from pyweathermap.switch_registration import get_all_switches

# Primary creation and operation function called by run_server.
# Creates Flask app with lazily-built, per-group maps.
# Defines /, /map/<name>, and /map/<name>/map.png routes.
def create_app(registry, default_center=None, refresh_interval: int = 60, traffic_interval: int = 300, startup: int = 60) -> Flask:
    app = Flask(__name__)
    app.config["REGISTRY"] = registry
    app.config["DEFAULT_CENTER"] = default_center
    app.config["INTERVAL"] = refresh_interval
    app.config["MAPS"] = {}             # group_id -> map entry (see _new_map_entry)
    app.config["MAPS_LOCK"] = threading.Lock()  # guards creation of new MAPS entries
    app.config["NOTICES"] = [] # list of notices about completed WeatherMaps
    app.config["NOTICES_LOCK"] = threading.Lock()

    @app.route("/")
    def root():
        switches = get_all_switches(registry)
        return render_template("index.html", switches=switches)
    
    @app.route("/goto")
    def goto():
        switch = request.args.get("switch")
        if not switch:
            abort(400)
        return redirect(f"/map/{switch.lower()}")

    # Defines primary page for a given switch/group's map.
    @app.route("/map/<name>")
    def show_map(name):
        _, canonical_name, _ = manager.resolve(app.config["REGISTRY"], name)
        group_id, entry = manager.get_or_create_map(app, app.config["REGISTRY"], name, traffic_interval, startup)

        with entry["lock"]:
            status = entry["status"]
            if status == "ready":
                m = entry["wmap"]
                last_updated = entry["updated"]
            error = entry["error"]

        if status == "loading":
            return render_template("loading.html", name=name)
        if status == "error":
            return render_template("error.html", name=name, error=error), 500

        n_areas = MapRenderer(m).get_node_areas()
        l_areas = MapRenderer(m).get_link_areas()
        return render_template(
            "map.html",
            title=m.title or "Network Weathermap",
            interval=refresh_interval,
            ts=int(time.time()),
            nodes=len(m.nodes),
            links=len(m.links),
            n_areas=n_areas,
            l_areas=l_areas,
            map_width=m.width,
            map_height=m.height,
            canonical_name=canonical_name,
            last_updated=datetime.fromtimestamp(last_updated).strftime("%Y-%m-%d %H:%M:%S"), 
        )

    # Resets a failed map's status and retriggers config_from_snmp in a new build thread.
    @app.route("/map/<name>/retry", methods=["POST"])
    def retry_map(name):
        manager.retry_map(app, app.config["REGISTRY"], name, traffic_interval, startup)
        return redirect(f"/map/{name}")

    # Defines route to display a given switch/group's rendered image.
    @app.route("/map/<name>/map.png")
    def map_png(name):
        _, entry = manager.get_or_create_map(app, app.config["REGISTRY"], name, traffic_interval, startup)
        with entry["lock"]:
            if entry["status"] != "ready":
                abort(404)
            data = entry["png"]
        return Response(data, mimetype="image/png")
    
    @app.route("/notices")
    def notices():
        since = request.args.get("since", 0.0, type=float)
        with app.config["NOTICES_LOCK"]:
            cutoff = time.time() - 60
            app.config["NOTICES"] = [n for n in app.config["NOTICES"] if n["ts"] > cutoff]
            recent = [n for n in app.config["NOTICES"] if n["ts"] > since]
        return "".join(f'<div class="toast" data-ts="{n["ts"]}">'
            f'Weathermap for <a href="{n["url"]}">{n["name"]}</a> is ready</div>'
            for n in recent)

    return app

# Controlling function called by main.py to intialize the server.
# Takes in command line arguments from user through main.py call.
# Builds app with create_app call and then runs.
def run_server(
    registry,
    default_center=None,
    host: str = "127.0.0.1",
    port: int = 8888,
    refresh_interval: int = 60,
    traffic_interval: int = 300,
    startup: int = 60,
    debug: bool = False,
):
    app = create_app(registry, default_center, refresh_interval, traffic_interval, startup)
    print(f"  Weathermap server: http://{host}:{port}/")
    app.run(host=host, port=port, debug=debug, use_reloader=False)