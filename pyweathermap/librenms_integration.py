import os
import requests
import time
from urllib.parse import quote
""" Allows for integration between LibreNMS page and Network Weathermap.
Functionality will simply be disabled if LibreNMS is not configured, 
rather than failing completely."""

cache = {}
community_cache = {}

# Retrieves the LibreNMS URL for the device with the given IP address
def get_device_url(ip):
    libre_url = os.environ.get("LIBRENMS_URL")
    api_key = os.environ.get("LIBRENMS_API_KEY")

    if not libre_url or not api_key:
        return None

    headers = {"X-Auth-Token": api_key}
    cached = cache.get(ip)
    if cached and cached[1] > time.time():
        return cached[0]

    try:
        resp = requests.get(
            f"{libre_url}/api/v0/devices/{quote(ip, safe='')}",
            headers=headers,
            params={"columns": "device_id"},
            timeout=10,
        )
        resp.raise_for_status()
        device_id = resp.json()["devices"][0]["device_id"]
        url = f"{libre_url}/device/{device_id}"
    except Exception:
        url = None
    ttl = 300 if url is None else 3600
    cache[ip] = (url, time.time() + ttl)
    return url

# Retrieves the SNMP community for the device with the given IP address.
def get_device_community(ip):
    now = time.time()
    cached = community_cache.get(ip)
    if cached and cached[1] > now:
        return cached[0]
    libre_url = os.environ.get("LIBRENMS_URL")
    api_key = os.environ.get("LIBRENMS_API_KEY")
    if not libre_url or not api_key:
        return None
    headers = {"X-Auth-Token": api_key}
    try:
        resp = requests.get(f"{libre_url}/api/v0/devices/{quote(ip, safe='')}", headers=headers, params={"columns": "community"}, timeout=10)
        resp.raise_for_status()
        community = resp.json()["devices"][0]["community"]
    except Exception:
        return None
    community_cache[ip] = (community, now + 300)
    return community