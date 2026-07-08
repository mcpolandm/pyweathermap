# pyweathermap

A Python network weathermap generator. Reads a plain-text file listing targeted switches, uses SNMP polling to collect connection data and stores it as a WeatherMap object, and produces a bandwidth-coloured network diagram — as a PNG image and/or a self-refreshing localhost website.

Inspired by [Network Weathermap](https://network-weathermap.com/) (PHP), re-implemented in Python with Pillow and Flask.

---

## Quick start

This project requires Python version 3.8 and above, though 3.9 is recommended. Full functionality is confirmed for version 3.11 and above.

```bash
# Install dependencies
pip3 install -r requirements.txt

# Declare environment variables for your setup:
# Default Center Node(s) for PNG:
export PYWEATHERMAP_DEFAULT_CENTER=switch1,switch2
# Switch information text file:
export PYWEATHERMAP_SWITCHES=/path/to/switches.txt

# Optional: Add integration to LibreNMS
# LibreNMS URL:
export LIBRENMS_URL=https://librenms.link.org
# LibreNMS API Key:
export LIBRENMS_API_KEY=apikeygoeshere

# Render default nodes to a PNG
python main.py

# Render to a PNG centered on specific node named switch3
python main.py --center switch3

# Render + start the web server at http://127.0.0.1:8888
python main.py --serve

# Render to a specific name PNG
python3 main.py --output examples/image.png
```

---

## Command-line options

| Flag | Default | Description |
|------|---------|-------------|
| `--output`, `-o` | `examples/Network Map {node name}.png` | Override the PNG output path |
| `--server`, `-s` | off | Start the web server |
| `--host` | `127.0.0.1` | Web server bind address |
| `--port`, `-p` | `8888` | Web server port |
| `--refresh` | `60` | Browser auto-refresh interval (seconds) |
| `--traffic` | `300` | Traffic data auto-refresh interval (seconds) | 

---

## Switch file format

The switch file format is highly simplistic. Each line represents one switch that will be polled, and represent a centerpoint on the WeatherMap. A line is formatted as such:

IP NAME COMMUNITY FILE GROUP

where:
 - IP represents the network IP address of the device
 - NAME represents the unique text name of the device
 - COMMUNITY represents the name of the SNMP community that can be used on the device for polling
 - FILE represents a csv file with one column of interface names and one column of connected device names. To use LLDP, input NONE here. Use this file for switches with LLDP disabled.
 - GROUP represents a group name for switches to be displayed together. Any switches with the same group name will be displayed in the same diagram. Switches with no group name will simply be shown alone.

 Example line from FILE:
 eth0,device1

## How rendering works

Each map is produced as a layered RGBA image rendered at 2× resolution and downscaled with Lanczos filtering for smooth edges:

1. **Background** — solid fill
2. **Links** — each link is two fat arrows meeting at the split point. The arrow from node 1 toward the midpoint is coloured by *out* utilisation; the arrow from node 2 toward the midpoint is coloured by *in* utilisation. Colours come from the active `SCALE`.
3. **Node shadows** — soft Gaussian drop-shadows behind each node shape.
4. **Nodes** — filled shapes (`box`, `rbox`, `round`) with a top-light highlight.
5. **Bandwidth labels** — pill-shaped labels on each arrow half showing the current throughput.
6. **Legend + Title + timestamp** — a continuous gradient bar showing 0–100 % with tick marks.

---

## Project layout

```
pyweathermap/
├── main.py                     CLI entry point
├── requirements.txt            Project dependencies
├── examples/
│   ├── simple.txt              Example switch file (One switch)
│   └── simple.png              Example output (7 nodes, 7 links)
└── pyweathermap/
    ├── config.py               Creates WeatherMap objects from switch registry
    ├── getting_traffic.py      Collects collection details for one switch
    ├── layout.py               Uses NetworkX library to determine Node positions
    ├── librenms_integration.py Creates links to LibreNMS page in WeatherMap if API information is configured 
    ├── map_server_manager.py   Processes new WeatherMap creation for live server
    ├── models.py               Dataclasses: WeatherMap, MapNode, MapLink, MapScale, Color, default_scale
    ├── renderer.py             Pillow-based image renderer (layered RGBA + 2× supersampling)
    ├── server.py               Flask web server — serves /  and /map.png
    └── switch_registration.py  Parses switch .txt files
```

---

## License

MIT — see [LICENSE](LICENSE).
