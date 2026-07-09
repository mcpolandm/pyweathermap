#!/usr/bin/env python3
"""
pyweathermap — Python network weathermap generator

Usage:
  python main.py                     # render to image only
  python main.py --serve             # render + web server
  python main.py --serve --port 9000 # custom port
  python main.py --output out.png    # override output path
"""

import argparse
import os
import pyweathermap.config as snmp_config
from pyweathermap.renderer import MapRenderer
from pyweathermap.server import run_server
import pyweathermap.switch_registration as registration

# Function call to run the PyWeatherMap system.
# Builds WeatherMap object through call to config.py
# If no live site is requested, build the diagram through MapRenderer and save to requested filename or default
# For live site, create though call to server.py
def main():
    # Add commandline arguments
    parser = argparse.ArgumentParser(
        description="Generate a network weathermap."
    )
    parser.add_argument("--output", "-o", default=None, help="Output PNG path (overrides config)")
    parser.add_argument("--host", default="127.0.0.1", help="Web server host (default: 127.0.0.1)")
    parser.add_argument("--port", "-p", type=int, default=8888, help="Web server port (default: 8888)")
    parser.add_argument("--refresh", type=int, default=60, help="Browser refresh interval in seconds (default: 60)")
    parser.add_argument("--traffic", type=int, default=300, help="Diagram traffic data refresh interval in seconds (default: 300)")
    parser.add_argument("--startup", type=int, default=60, help="Diagram traffic data wait time for initial startup in seconds (server only, default: 60)")
    parser.add_argument("--center", type=str, default=None, help="Starting switch for the server to display")
    parser.add_argument("--server", "-s", action="store_true", help="Start the web server")
    args = parser.parse_args()

    switches_path = os.environ.get("PYWEATHERMAP_SWITCHES")
    if not switches_path:
        raise RuntimeError(
            "PYWEATHERMAP_SWITCHES is not set. "
            "Set it to the path of your switch_list.txt, e.g. "
            "'export PYWEATHERMAP_SWITCHES=/path/to/switch_list.txt'"
        )
    switch_registry = registration.load_switch_registry(switches_path)
    # Run server on startup if requested.
    if args.server:
        run_server(switch_registry, default_center=args.center, host=args.host, port=args.port, refresh_interval=args.refresh, traffic_interval=args.traffic, startup=args.startup)
    else:
        center_switches = registration.get_center_nodes(switch_registry, args.center, seconds=args.traffic)
        # WeatherMap construction through config_from_snmp call
        print(f"  Parsing configuration through SNMP...")
        wmap = snmp_config.config_from_snmp(switch_registry, center_switches)
        print(f"  Map: {wmap.width}x{wmap.height}  nodes={len(wmap.nodes)}  links={len(wmap.links)}")

        # Determine output path
        out_path = args.output
        # If path provided by user, save there
        if out_path:
            print(f"  Rendering image → {out_path}")
            renderer = MapRenderer(wmap)
            img = renderer.render()
            img.save(out_path)
            print(f"  Saved: {out_path}")
        # If no path and not server, save to default path (examples/WeatherMap title)
        else:
            out_path = "examples/" + wmap.title + ".png"
            print(f"  Rendering image → {out_path}")
            renderer = MapRenderer(wmap)
            img = renderer.render()
            img.save(out_path)
            print(f"  Saved: {out_path}")

if __name__ == "__main__":
    main()