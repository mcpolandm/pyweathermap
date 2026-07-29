import hmac
import hashlib
import os

# Used to ensure the accessor of the /get route has the secret key.
# Needed to limit requests from unknown users to keep data safe.
# Uses HMAC and SHA256 to encode with secret key, IP, and community.

def _hmac(*parts):
    msg = b"\x00".join(p.encode() for p in parts)
    return hmac.new(_secret().encode(), msg, hashlib.sha256).hexdigest()[:16]

def _secret():
    return os.environ.get("PYWEATHERMAP_GET_SECRET")

def enabled():
    return bool(_secret())

def sign(ip, community):
    return _hmac("get", ip, community)

def sign_ip(ip):
    return _hmac("get-ip", ip)

def verify(ip, community, sig):
    if community is not None:
        return enabled() and bool(sig) and hmac.compare_digest(sig, sign(ip, community))
    return enabled() and bool(sig) and hmac.compare_digest(sig, sign_ip(ip))