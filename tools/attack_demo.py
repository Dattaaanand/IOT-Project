"""Security demo: plays an attacker on the public broker. The IoT device must refuse all of it.

    python tools/attack_demo.py unsigned    # command with no signature
    python tools/attack_demo.py wrongkey    # command signed with a guessed key
    python tools/attack_demo.py tampered    # genuine command with the value changed
    python tools/attack_demo.py replay      # capture the next genuine command and send it again

Watch the device window for "[SECURITY] REJECTED" and the dashboard for a FORGED_COMMAND alert.
"""
import json
import os
import sys
import time
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import paho.mqtt.client as mqtt  # noqa: E402

from common.config import load_config  # noqa: E402
from common.security import sign  # noqa: E402

MODES = ("unsigned", "wrongkey", "tampered", "replay")


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    if mode not in MODES:
        sys.exit(__doc__)
    cfg = load_config()
    topic = cfg["topics"]["command"]
    captured = []
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"attacker-{uuid.uuid4().hex[:6]}")
    client.on_message = lambda c, u, m: captured.append(json.loads(m.payload.decode()))
    client.connect(cfg["mqtt"]["host"], cfg["mqtt"]["port"])
    client.loop_start()
    time.sleep(1.5)

    fake = {"cmd_id": f"attack-{uuid.uuid4().hex[:6]}", "ts": int(time.time() * 1000), "source": "attacker",
            "action": "isolate_zone", "target": "A", "value": None, "reason": "attacker wants to cut supply"}
    if mode == "unsigned":
        msg = fake
    elif mode == "wrongkey":
        msg = {**fake, "sig": sign(fake, "guessed-key")}
    else:
        print("Waiting for a genuine command (click e.g. 'Restore all' on the dashboard) ...")
        client.subscribe(topic, qos=1)
        while not captured:
            time.sleep(0.2)
        genuine = captured[0]
        print(f"Captured: {genuine['action']} {genuine.get('target') or ''}")
        time.sleep(2)  # let the device execute the genuine one first
        msg = {**genuine, "action": "isolate_zone", "target": "A"} if mode == "tampered" else genuine
    print(f"Sending {mode} command: {msg['action']} {msg.get('target') or ''}")
    client.publish(topic, json.dumps(msg), qos=1).wait_for_publish(5)
    time.sleep(1)
    client.loop_stop()
    client.disconnect()


if __name__ == "__main__":
    main()
