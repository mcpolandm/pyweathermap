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
from flask import Flask, Response, render_template_string, redirect, abort, request

from .renderer import MapRenderer
import pyweathermap.map_server_manager as manager
from pyweathermap.switch_registration import get_all_switches

INDEX_TEMPLATE = """<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <title>pyweathermap</title>
  <style>
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      background: #1a1a2e;
      color: #eee;
      font-family: 'Segoe UI', system-ui, sans-serif;
      display: flex;
      align-items: center;
      justify-content: center;
      min-height: 100vh;
    }
    .card {
      background: #16213e;
      border: 1px solid #0f3460;
      border-radius: 8px;
      padding: 32px 40px;
      text-align: center;
      box-shadow: 0 4px 24px rgba(0,0,0,0.5);
    }
    .toggle {
      display: flex;
      gap: 24px;
      justify-content: center;
      margin-bottom: 20px;
    }
    .toggle label {
      display: inline-flex;
      align-items: center;
      gap: 6px;
      padding: 4px 8px;
    }
    form {
      display: flex;
      justify-content: center;
      align-items: center;
      gap: 16px;
    }

    h1 { font-size: 1.3rem; color: #e94560; margin-bottom: 20px; }
    select, button {
      font-size: 1rem;
      padding: 8px 12px;
      border-radius: 4px;
      border: 1px solid #0f3460;
    }
    select {
      background: #1a1a2e;
      color: #eee;
      min-width: 220px;
      margin-right: 8px;
    }
    button {
      background: #e94560;
      color: #fff;
      border: none;
      cursor: pointer;
    }
    button:hover { background: #d63a52; }
  </style>
</head>
<body>
  <div class="card">
    <h1>Network Weathermap</h1>
    <div class="toggle">
      <label><input type="radio" name="display_mode" value="name" checked> Name</label>
      <label><input type="radio" name="display_mode" value="ip"> IP</label>
    </div>
    <form action="/goto" method="get">
      <select name="switch" id="switch-select">
        {% for name, ip in switches %}
        <option value="{{ name }}" data-name="{{ name }}" data-ip="{{ ip }}">{{ name }}</option>
        {% endfor %}
      </select>
      <button type="submit">View Map</button>
    </form>
<script>
  document.querySelectorAll('input[name="display_mode"]').forEach(radio => {
    radio.addEventListener('change', e => {
      const mode = e.target.value;
      document.querySelectorAll('#switch-select option').forEach(opt => {
        const val = mode === 'ip' ? opt.dataset.ip : opt.dataset.name;
        opt.value = val;
        opt.textContent = val;
      });
    });
  });
</script>

  </div>
</body>
</html>
"""

# HTML template for the built map page
HTML_TEMPLATE = """<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <title>{{ title }}</title>
  <meta http-equiv="refresh" content="{{ interval }}">
  <style>
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      background: #1a1a2e;
      color: #eee;
      font-family: 'Segoe UI', system-ui, sans-serif;
      display: flex;
      flex-direction: column;
      min-height: 100vh;
    }
    header {
      background: #16213e;
      padding: 12px 20px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      border-bottom: 1px solid #0f3460;
    }
    header h1 { font-size: 1.2rem; color: #e94560; }
    header .meta { font-size: 0.8rem; color: #888; }
    .map-wrap {
      flex: 1;
      display: flex;
      align-items: center;
      justify-content: center;
      padding: 20px;
    }
    .image-frame {
      position: relative;
      display: inline-block;
      max-width: 100%;
    }
    .image-frame img {
      display: block;
      max-width: 100%;
      height: auto;
      border: 1px solid #0f3460;
      border-radius: 4px;
      box-shadow: 0 4px 24px rgba(0,0,0,0.5);
    }
    .node-link {
      position: absolute;
      display: block;
    }
    footer {
      background: #16213e;
      padding: 8px 20px;
      font-size: 0.75rem;
      color: #555;
      border-top: 1px solid #0f3460;
      display: flex;
      justify-content: space-between;
    }
    .dot {
      display: inline-block;
      width: 8px; height: 8px;
      border-radius: 50%;
      background: #2ecc71;
      margin-right: 6px;
      animation: pulse 2s infinite;
    }
    .home-link {
      color: #e94560;
      text-decoration: none;
    }
    .home-link:hover {
      text-decoration: underline;
    }
    @keyframes pulse {
      0%,100% { opacity: 1; }
      50% { opacity: 0.3; }
    }
  </style>
</head>
<body>
  <header>
    <a href="/" class="home-link">← Home</a>
    <h1>{{ title or "Network Weathermap" }}</h1>
    <span class="meta"><span class="dot"></span>Auto-refreshes every {{ interval }}s</span>
  </header>
  <div class="map-wrap">
    <div class="image-frame">
      <img src="/map/{{ canonical_name }}/map.png?t={{ ts }}" alt="Network Weathermap">
      {% for name, x1, y1, x2, y2, url in areas %}
      <a class="node-link"
         style="left:{{ (x1 / map_width * 100) }}%; top:{{ (y1 / map_height * 100) }}%; width:{{ ((x2 - x1) / map_width * 100) }}%; height:{{ ((y2 - y1) / map_height * 100) }}%;"
         href="{{ url }}" target="_self" title="{{ name }}"></a>
      {% endfor %}
    </div>
  </div>
  <footer>
    <span>{{ nodes }} nodes &nbsp;·&nbsp; {{ links }} links</span>
    <span>Last Updated: {{ last_updated }}</span>
    <span>pyweathermap</span>
  </footer>
</body>
</html>
"""

# HTML template shown while a map is still being built (blocks ~1 SNMP sample window)
LOADING_TEMPLATE = """<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <title>Building map…</title>
  <meta http-equiv="refresh" content="4">
  <style>
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      background: #1a1a2e;
      color: #eee;
      font-family: 'Segoe UI', system-ui, sans-serif;
      display: flex;
      align-items: center;
      justify-content: center;
      min-height: 100vh;
    }
    .card { text-align: center; }
    .dot {
      display: inline-block;
      width: 10px; height: 10px;
      border-radius: 50%;
      background: #e94560;
      margin-right: 8px;
      animation: pulse 1s infinite;
    }
    .home-link {
      color: #e94560;
      text-decoration: none;
    }
    .home-link:hover {
      text-decoration: underline;
    }

    @keyframes pulse { 0%,100% { opacity: 1; } 50% { opacity: 0.3; } }
  </style>
</head>
<body>
  <a href="/" class="home-link">← Home</a>
  <div class="card">
    <p><span class="dot"></span>Building weathermap for {{ name }}…</p>
    <p style="font-size:0.85rem;color:#888;margin-top:8px;">
      Sampling live traffic — this page refreshes automatically.
    </p>
  </div>
</body>
</html>
"""

# HTML template shown if a map failed to build
ERROR_TEMPLATE = """<!DOCTYPE html>
<html>
<head><a href="/" style="color:#e94560; text-decoration:none;">← Home</a><meta charset="utf-8"><title>Map failed</title></head>
<body style="background:#1a1a2e;color:#eee;font-family:sans-serif;padding:40px;">
  <h2>Failed to build map for {{ name }}</h2>
  <pre style="color:#e94560;white-space:pre-wrap;">{{ error }}</pre>
</body>
</html>
"""

# Primary creation and operation function called by run_server.
# Creates Flask app with lazily-built, per-group maps.
# Defines /, /map/<name>, and /map/<name>/map.png routes.
def create_app(registry, default_center=None, refresh_interval: int = 60, traffic_interval: int = 300) -> Flask:
    app = Flask(__name__)
    app.config["REGISTRY"] = registry
    app.config["DEFAULT_CENTER"] = default_center
    app.config["INTERVAL"] = refresh_interval
    app.config["MAPS"] = {}             # group_id -> map entry (see _new_map_entry)
    app.config["MAPS_LOCK"] = threading.Lock()  # guards creation of new MAPS entries

    @app.route("/")
    def root():
        switches = get_all_switches(registry)
        return render_template_string(INDEX_TEMPLATE, switches=switches)
    
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
        group_id, entry = manager.get_or_create_map(app, app.config["REGISTRY"], name, traffic_interval)

        with entry["lock"]:
            status = entry["status"]
            if status == "ready":
                m = entry["wmap"]
                last_updated = entry["updated"]
            error = entry["error"]

        if status == "loading":
            return render_template_string(LOADING_TEMPLATE, name=name)
        if status == "error":
            return render_template_string(ERROR_TEMPLATE, name=name, error=error), 500

        areas = MapRenderer(m).get_node_areas()
        return render_template_string(
            HTML_TEMPLATE,
            title=m.title or "Network Weathermap",
            interval=refresh_interval,
            ts=int(time.time()),
            nodes=len(m.nodes),
            links=len(m.links),
            areas=areas,
            map_width=m.width,
            map_height=m.height,
            canonical_name=canonical_name,
            last_updated=datetime.fromtimestamp(last_updated).strftime("%Y-%m-%d %H:%M:%S"),
        )

    # Defines route to display a given switch/group's rendered image.
    @app.route("/map/<name>/map.png")
    def map_png(name):
        _, entry = manager.get_or_create_map(app, app.config["REGISTRY"], name, traffic_interval)
        with entry["lock"]:
            if entry["status"] != "ready":
                abort(404)
            data = entry["png"]
        return Response(data, mimetype="image/png")

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
    debug: bool = False,
):
    app = create_app(registry, default_center, refresh_interval, traffic_interval)
    print(f"  Weathermap server: http://{host}:{port}/")
    app.run(host=host, port=port, debug=debug, use_reloader=False)