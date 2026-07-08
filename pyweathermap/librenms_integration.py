import os
import requests

cache = {}

def get_device_url(ip):
    libre_url = os.environ.get("LIBRENMS_URL")
    api_key = os.environ.get("LIBRENMS_API_KEY")

    if not libre_url or not api_key:
        return None

    headers = {"X-Auth-Token": api_key}
    if ip in cache:
        return cache[ip]

    try:
        resp = requests.get(
            f"{libre_url}/api/v0/devices/{ip}",
            headers=headers,
            params={"columns": "device_id"},
            timeout=10,
        )
        resp.raise_for_status()
        device_id = resp.json()["devices"][0]["device_id"]
        url = f"{libre_url}/device/{device_id}"
    except Exception:
        url = None
    cache[ip] = url
    return url