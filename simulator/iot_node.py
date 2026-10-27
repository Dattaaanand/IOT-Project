"""IoT field node: sensor publisher + actuator subscriber.

    python -m simulator.iot_node                 # play the playlist from config.json
    python -m simulator.iot_node --scenario leak1 --rate 10

Sensor side  (arrow 1): replays the real WDSEventDB recordings row by row and
                        publishes each reading as JSON on `wds/sensors/telemetry`.
Actuator side (arrow 8): subscribes to `wds/control/cmd`, operates pumps,
                        isolation valves and safe-mode after a realistic actuator
                        delay, and acknowledges on `wds/actuators/state`.

Closed loop on recorded data
----------------------------
A recording cannot react to a closed valve, so the effect of a control action is
approximated: if the section with a leak is isolated (or safe mode blocks a
cyber-attack) the replay skips the rest of that anomalous run after the valve
travel time, i.e. the network returns to normal operation. Pump-speed changes
and sensor flags are recorded in the actuator state but do not alter the data.
"""
import argparse
import json
import threading
import time

import paho.mqtt.client as mqtt

from common.config import CLASS_NAMES, CYBER_ATTACK, LEAK, NORMAL, SCENARIOS, SENSORS, ZONES, load_config
from common.dataset import load_scenario

DEVICE_ID = "wds-node-01"


def now_ms():
    return int(time.time() * 1000)


class IoTNode:
    def __init__(self, cfg, playlist, rate_hz):
        self.cfg = cfg
        self.topics = cfg["topics"]
        self.sim = cfg["simulator"]
        self.rate_hz = rate_hz
        self.playlist = playlist
        self.data = {}
        self.lock = threading.Lock()

        # playback position
        self.pl_index = -1
        self.segment = 0
        self.df = None
        self.row = 0
        self.end = 0
        self.scenario = None
        self.seq = 0
        self.paused = False
        self.pending_skip = None  # (time to apply, reason)

        # actuator state (pump speed in % of the normal setpoint)
        self.state = {
            "mode": "AUTO",
            "pumps": {"pump1": 100, "pump3": 100, "pump4": 100},
            "valves": {z["valve"]: "OPEN" for z in ZONES.values()},
            "flagged_sensors": [],
        }

        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=DEVICE_ID)
        self.client.will_set(self.topics["sim_status"], json.dumps({"device_id": DEVICE_ID, "online": False}),
                             qos=1, retain=True)
        self.client.on_connect = self.on_connect
        self.client.on_message = self.on_message

    # ------------------------------------------------------------------ data
    def get(self, name):
        if name not in self.data:
            self.data[name] = load_scenario(name, self.cfg["dataset_dir"])
        return self.data[name]

    def start_item(self, item):
        df = self.get(item["scenario"])
        self.df = df
        self.scenario = item["scenario"]
        self.row = item.get("start", 0) or 0
        self.end = min(item.get("end") or len(df), len(df))
        self.segment += 1
        self.pending_skip = None
        print(f"[sim] segment {self.segment}: {self.scenario} rows {self.row}-{self.end}")
        self.publish_status()

    def next_item(self):
        self.pl_index = (self.pl_index + 1) % len(self.playlist)
        self.start_item(self.playlist[self.pl_index])

    def current_class(self):
        return int(self.df["gt_class"].iat[self.row]) if self.row < self.end else NORMAL

    def skip_anomaly(self, reason):
        """Fast-forward to the end of the current anomalous run (event contained)."""
        cls = self.current_class()
        start = self.row
        while self.row < self.end and int(self.df["gt_class"].iat[self.row]) == cls:
            self.row += 1
        skipped = self.row - start
        print(f"[sim] {reason}: skipped {skipped} anomalous readings")
        self.publish_ack({"cmd_id": None, "action": "process_effect", "target": self.scenario,
                          "value": skipped, "effect": reason}, None)

    # ------------------------------------------------------------------ MQTT
    def on_connect(self, client, userdata, flags, reason_code, properties):
        print(f"[sim] connected to broker ({reason_code})")
        client.subscribe([(self.topics["command"], 1), (self.topics["sim_control"], 1)])
        self.publish_state()
        self.publish_status()

    def on_message(self, client, userdata, msg):
        try:
            payload = json.loads(msg.payload.decode())
        except ValueError:
            print(f"[sim] ignored non-JSON message on {msg.topic}")
            return
        if msg.topic == self.topics["command"]:
            delay = self.sim["actuator_delay_ms"] / 1000
            threading.Timer(delay, self.execute_command, args=(payload,)).start()
        elif msg.topic == self.topics["sim_control"]:
            self.sim_control(payload)

    def execute_command(self, cmd):
        """Actuator: apply one control command and acknowledge it."""
        action, target, value = cmd.get("action"), cmd.get("target"), cmd.get("value")
        effect = None
        with self.lock:
            s = self.state
            if action == "set_pump" and target in s["pumps"]:
                s["pumps"][target] = float(value)
            elif action in ("isolate_zone", "close_valve"):
                valve = ZONES[target]["valve"] if action == "isolate_zone" else target
                if valve in s["valves"]:
                    s["valves"][valve] = "CLOSED"
                    if self.current_class() == LEAK:
                        effect = "leak_isolated"
            elif action == "open_valve" and target in s["valves"]:
                s["valves"][target] = "OPEN"
            elif action == "safe_mode":
                s["mode"] = "SAFE" if value in (True, 1, "on", "ON") else "AUTO"
                if s["mode"] == "SAFE" and self.current_class() == CYBER_ATTACK:
                    effect = "attack_blocked"
            elif action == "flag_sensor" and target not in s["flagged_sensors"]:
                s["flagged_sensors"].append(target)
            elif action == "unflag_sensor" and target in s["flagged_sensors"]:
                s["flagged_sensors"].remove(target)
            elif action == "restore":
                s["mode"] = "AUTO"
                s["pumps"] = {k: 100 for k in s["pumps"]}
                s["valves"] = {k: "OPEN" for k in s["valves"]}
                s["flagged_sensors"] = []
            elif action == "alert_only":
                pass
            else:
                print(f"[sim] unknown command: {cmd}")
                return
            if effect:
                self.pending_skip = (time.time() + self.sim["valve_travel_s"], effect)
        print(f"[sim] actuator <- {action} {target or ''} {'' if value is None else value}"
              f"{'  (' + effect + ' pending)' if effect else ''}")
        self.publish_ack(cmd, effect)

    def publish_ack(self, cmd, effect):
        ack = {"type": "ack", "device_id": DEVICE_ID, "cmd_id": cmd.get("cmd_id"),
               "action": cmd.get("action"), "target": cmd.get("target"), "value": cmd.get("value"),
               "source": cmd.get("source"), "cmd_ts": cmd.get("ts"), "ack_ts": now_ms(),
               "effect": effect or cmd.get("effect"), "state": self.state}
        self.client.publish(self.topics["actuator_state"], json.dumps(ack), qos=1)
        self.publish_state()

    def publish_state(self):
        msg = {"type": "state", "device_id": DEVICE_ID, "ts": now_ms(), "state": self.state}
        self.client.publish(self.topics["actuator_state"] + "/current", json.dumps(msg), qos=1, retain=True)

    def publish_status(self):
        if self.df is None:
            return
        msg = {"device_id": DEVICE_ID, "online": True, "scenario": self.scenario, "segment": self.segment,
               "row": self.row, "end": self.end, "rate_hz": self.rate_hz, "paused": self.paused,
               "ts": now_ms()}
        self.client.publish(self.topics["sim_status"], json.dumps(msg), qos=1, retain=True)

    def sim_control(self, c):
        """Dashboard controls for the demo: inject a scenario, change speed, pause."""
        action = c.get("action")
        with self.lock:
            if action == "play" and c.get("scenario") in SCENARIOS:
                self.start_item({"scenario": c["scenario"], "start": c.get("start"), "end": c.get("end")})
            elif action == "rate":
                self.rate_hz = max(0.5, min(50.0, float(c.get("value", self.rate_hz))))
            elif action == "pause":
                self.paused = True
            elif action == "resume":
                self.paused = False
            elif action == "next":
                self.next_item()
        print(f"[sim] control: {c}")
        self.publish_status()

    # ------------------------------------------------------------------ main loop
    def publish_reading(self):
        r = self.df.iloc[self.row]
        gt = int(r["gt_class"])
        msg = {"device_id": DEVICE_ID, "seq": self.seq, "ts": now_ms(), "scenario": self.scenario,
               "segment": self.segment, "row": int(self.row),
               **{c: round(float(r[c]), 4) for c in SENSORS},
               "gt_class": gt, "gt_name": CLASS_NAMES[gt]}
        self.client.publish(self.topics["telemetry"], json.dumps(msg), qos=0)
        self.seq += 1

    def run(self):
        host, port = self.cfg["mqtt"]["host"], self.cfg["mqtt"]["port"]
        print("[sim] loading recordings ...")
        for item in self.playlist:
            self.get(item["scenario"])
        self.client.connect(host, port, keepalive=30)
        self.client.loop_start()
        self.next_item()
        next_t = time.time()
        last_status = 0
        try:
            while True:
                with self.lock:
                    if self.pending_skip and time.time() >= self.pending_skip[0]:
                        reason = self.pending_skip[1]
                        self.pending_skip = None
                        self.skip_anomaly(reason)
                    if self.row >= self.end:
                        self.next_item()
                    if not self.paused:
                        self.publish_reading()
                        self.row += 1
                    period = 1.0 / self.rate_hz
                if time.time() - last_status > 2:
                    self.publish_status()
                    last_status = time.time()
                next_t += period
                time.sleep(max(0.0, next_t - time.time()))
                if time.time() - next_t > 1:  # fell behind (e.g. laptop asleep)
                    next_t = time.time()
        except KeyboardInterrupt:
            pass
        finally:
            self.client.publish(self.topics["sim_status"], json.dumps({"device_id": DEVICE_ID, "online": False}),
                                qos=1, retain=True)
            self.client.loop_stop()
            self.client.disconnect()


def main():
    cfg = load_config()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default=cfg["mqtt"]["host"])
    ap.add_argument("--port", type=int, default=cfg["mqtt"]["port"])
    ap.add_argument("--rate", type=float, default=cfg["simulator"]["rate_hz"], help="readings per second")
    ap.add_argument("--scenario", help="loop a single recording instead of the playlist")
    args = ap.parse_args()
    cfg["mqtt"].update(host=args.host, port=args.port)
    playlist = [{"scenario": args.scenario}] if args.scenario else cfg["simulator"]["playlist"]
    IoTNode(cfg, playlist, args.rate).run()


if __name__ == "__main__":
    main()
