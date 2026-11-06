"""Authentication of control commands (HMAC-SHA256 with a shared key).

The broker is public, so anyone could publish to the command topic. Every command
therefore carries `sig` = HMAC-SHA256(key, cmd_id|ts|action|target|value). The IoT
device recomputes it and rejects commands with a wrong or missing signature, and
also rejects old or already-seen commands (replay attack).
The same message format is used by Node-RED (dashboard commands) and the device.
"""
import hashlib
import hmac
import json


def signed_text(cmd):
    target = cmd.get("target")
    return "|".join([str(cmd.get("cmd_id") or ""), str(cmd.get("ts") or ""), cmd.get("action") or "",
                     "" if target is None else str(target), json.dumps(cmd.get("value"))])


def sign(cmd, key):
    return hmac.new(key.encode(), signed_text(cmd).encode(), hashlib.sha256).hexdigest()
