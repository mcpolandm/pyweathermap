import hmac
import hashlib
import os

"""Used to ensure the accessor of the /get route has the secret key.
Needed to limit requests from unknown users to keep data safe."""

def _secret():
    return os.environ.get("PYWEATHERMAP_GET_SECRET")

def enabled():
    return bool(_secret())

def sign(ip, community):
    return hmac.new(_secret().encode(), f"{ip}:{community}".encode(), hashlib.sha256).hexdigest()[:16]

def verify(ip, community, sig):
    return enabled() and bool(sig) and hmac.compare_digest(sig, sign(ip, community))