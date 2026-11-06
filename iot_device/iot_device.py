"""IoT field device of the Smart Water Distribution project (runs on the device laptop).

    python iot_device.py                     # play the playlist in device_config.json
    python iot_device.py --scenario leak1    # loop one recording
    python iot_device.py --rate 10           # readings per second
    python iot_device.py --random            # random events, each with normal readings around it

Sensors  : replays the real WDSEventDB recordings row by row and publishes each
           reading as JSON to the online MQTT broker.
Actuators: receives control commands from the cloud laptop, operates pumps, isolation
           valves and safe mode after a realistic delay, and sends an acknowledgement.
Results  : shows the alerts and the system state decided by the cloud, and keeps them
           in results_log.txt.

A recording cannot react to a closed valve, so the effect of a control action is
approximated: if a leak is isolated (or safe mode blocks a cyber-attack) the replay
skips the rest of that anomalous run after the valve travel time, i.e. the network
returns to normal. Pump-speed changes and sensor flags do not alter the data.
"""
import argparse
import csv
import hashlib
import hmac
import json
import os
import random
import sys
import threading
import time
import uuid
from collections import deque

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "vendor"))  # bundled paho-mqtt, no pip install needed

import paho.mqtt.client as mqtt  # noqa: E402

DEVICE_ID = "wds-node-01"
CLASS_NAMES = ["NORMAL", "LEAK", "SENSOR_FAULT", "CYBER_ATTACK"]
NORMAL, LEAK, SENSOR_FAULT, CYBER_ATTACK = range(4)
SENSORS = ["p1_out", "p2_out", "p3_in", "p4_in", "f1", "f2", "f3", "f4",
           "vfd1", "vfd2", "vfd3", "vfd4_1", "vfd4_2", "valve1", "valve2"]
ZONE_VALVES = {"A": "iso_A", "B": "iso_B", "C": "iso_C", "D": "iso_D"}
EVENT_RECORDINGS = ["leak1", "leak2", "leak3", "sensor1", "sensor23", "sensor45", "sensor67", "cyber"]


def now_ms():
    return int(time.time() * 1000)


def signed_text(cmd):
    """Same message format as common/security.py on the cloud laptop."""
    target = cmd.get("target")
    return "|".join([str(cmd.get("cmd_id") or ""), str(cmd.get("ts") or ""), cmd.get("action") or "",
                     "" if target is None else str(target), json.dumps(cmd.get("value"))])


def load_recording(name):
    with open(os.path.join(HERE, "recordings", f"{name}.csv"), newline="") as f:
        return [{k: float(v) for k, v in row.items()} for row in csv.DictReader(f)]


class IoTDevice:
    def __init__(self, cfg, playlist, rate_hz, random_mode=False):
        self.cfg = cfg
        self.topics = cfg["topics"]
        self.rate_hz = rate_hz
        self.playlist = playlist
        self.data = {}
        self.lock = threading.Lock()
        self.log_file = open(os.path.join(HERE, "results_log.txt"), "a", encoding="utf-8")

        # playback position
        self.pl_index = -1
        self.random_mode = random_mode
        self._clips = None
        self.last_clip = None
        self.segment = 0
        self.rows = None
        self.row = 0
        self.end = 0
        self.scenario = None
        self.seq = 0
        self.paused = False
        self.pending_skip = None  # (time to apply, reason)
        self.cloud_state = None
        self.last_cloud_msg = 0
        self.seen_cmd_ids = deque(maxlen=1000)  # replay protection
        self.rejected = 0
        self.last_security_alert = 0

        # actuator state (pump speed in % of the normal setpoint)
        self.state = {
            "mode": "AUTO",
            "pumps": {"pump1": 100, "pump3": 100, "pump4": 100},
            "valves": {v: "OPEN" for v in ZONE_VALVES.values()},
            "flagged_sensors": [],
        }

        # client ids must be unique on a public broker, or clients disconnect each other
        client_id = f"{cfg['project_id']}-device-{uuid.uuid4().hex[:6]}"
        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=client_id)
        self.client.will_set(self.topics["sim_status"], json.dumps({"device_id": DEVICE_ID, "online": False}),
                             qos=1, retain=True)
        self.client.reconnect_delay_set(1, 10)
        self.client.on_connect = self.on_connect
        self.client.on_disconnect = self.on_disconnect
        self.client.on_message = self.on_message

    def say(self, text):
        line = f"{time.strftime('%H:%M:%S')}  {text}"
        print(line, flush=True)
        self.log_file.write(line + "\n")
        self.log_file.flush()

    # ------------------------------------------------------------------ data
    def get(self, name):
        if name not in self.data:
            self.data[name] = load_recording(name)
        return self.data[name]

    def start_item(self, item):
        self.rows = self.get(item["scenario"])
        self.scenario = item["scenario"]
        self.row = item.get("start") or 0
        self.end = min(item.get("end") or len(self.rows), len(self.rows))
        self.segment += 1
        self.pending_skip = None
        self.say(f"[DATA]    now playing '{self.scenario}' readings {self.row}-{self.end}")
        self.publish_status()

    def next_item(self):
        if self.random_mode:
            self.start_item(self.random_item())
            return
        self.pl_index = (self.pl_index + 1) % len(self.playlist)
        self.start_item(self.playlist[self.pl_index])

    def event_clips(self):
        """Every anomalous run in the event recordings: (recording, first row, end row)."""
        if self._clips is None:
            self._clips = []
            for name in EVENT_RECORDINGS:
                rows, i = self.get(name), 0
                while i < len(rows):
                    if int(rows[i]["gt_class"]) != NORMAL:
                        j = i
                        while j < len(rows) and int(rows[j]["gt_class"]) != NORMAL:
                            j += 1
                        self._clips.append((name, i, j))
                        i = j
                    else:
                        i += 1
        return self._clips

    def random_item(self):
        """Random mode for a short demo: one random event from an event recording, with a
        random 10-20 s of that recording's own normal readings before it and a few seconds
        of normal readings after it. The next clip is a different event."""
        lo, hi = self.cfg["device"].get("random_lead_s", [10, 20])
        lead = int(random.uniform(lo, hi) * self.rate_hz)
        clips = [c for c in self.event_clips() if c[1] >= lead] or self.event_clips()  # need a normal lead-in
        kind = lambda c: int(self.get(c[0])[c[1]]["gt_class"])
        # pick the event type first (leak / sensor fault / cyber-attack), never the same type twice in a row
        kinds = sorted({kind(c) for c in clips} - ({kind(self.last_clip)} if self.last_clip else set()))
        chosen = random.choice(kinds) if kinds else kind(self.last_clip)
        name, onset, end = self.last_clip = random.choice([c for c in clips if kind(c) == chosen])
        return {"scenario": name, "start": onset - lead if onset >= lead else 0,
                "end": min(len(self.get(name)), end + int(8 * self.rate_hz))}

    def current_class(self):
        return int(self.rows[self.row]["gt_class"]) if self.row < self.end else NORMAL

    def skip_anomaly(self, reason):
        """Fast-forward to the end of the current anomalous run (event contained)."""
        cls = self.current_class()
        start = self.row
        while self.row < self.end and int(self.rows[self.row]["gt_class"]) == cls:
            self.row += 1
        skipped = self.row - start
        self.say(f"[EFFECT]  {reason}: {skipped} anomalous readings no longer happen")
        self.publish_ack({"cmd_id": None, "action": "process_effect", "target": self.scenario,
                          "value": skipped, "effect": reason}, None)

    # ------------------------------------------------------------------ MQTT
    def on_connect(self, client, userdata, flags, reason_code, properties):
        if reason_code != 0:
            self.say(f"[BROKER]  connection refused: {reason_code}")
            return
        self.say(f"[BROKER]  connected to {self.cfg['mqtt']['host']}")
        client.subscribe([(self.topics["command"], 1), (self.topics["sim_control"], 1),
                          (self.topics["alert"], 1), (self.topics["prediction"], 0),
                          (self.topics["ping"], 0)])
        self.publish_state()
        self.publish_status()

    def on_disconnect(self, client, userdata, flags, reason_code, properties):
        self.say(f"[BROKER]  disconnected ({reason_code}), reconnecting ...")

    def on_message(self, client, userdata, msg):
        try:
            payload = json.loads(msg.payload.decode())
        except ValueError:
            return
        t = msg.topic
        if t == self.topics["command"]:
            problem = self.check_command(payload)
            if problem:
                self.reject_command(payload, problem)
                return
            self.say(f"[COMMAND] from {payload.get('source')}: {payload.get('action')} "
                     f"{payload.get('target') or ''} {'' if payload.get('value') is None else payload.get('value')}"
                     f"  ({payload.get('reason', '')})")
            delay = self.cfg["device"]["actuator_delay_ms"] / 1000
            threading.Timer(delay, self.execute_command, args=(payload,)).start()
        elif t == self.topics["ping"]:
            self.client.publish(self.topics["pong"], json.dumps({"ping_ts": payload["ping_ts"], "device_ts": now_ms()}))
        elif t == self.topics["sim_control"]:
            self.sim_control(payload)
        elif t == self.topics["alert"]:
            self.say(f"[ALERT]   {payload.get('level')} {payload.get('type')}: {payload.get('message')}")
        elif t == self.topics["prediction"]:
            self.show_prediction(payload)

    def check_command(self, cmd):
        """Return why a command must be refused, or None if it is genuine."""
        sec = self.cfg["security"]
        sig = cmd.get("sig")
        if not sig:
            return "no signature"
        expected = hmac.new(sec["command_key"].encode(), signed_text(cmd).encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(str(sig), expected):
            return "invalid signature (forged or altered)"
        if cmd.get("cmd_id") in self.seen_cmd_ids:
            return "already executed (replay attack)"
        age_s = abs(now_ms() - int(cmd.get("ts") or 0)) / 1000
        if age_s > sec["max_command_age_s"]:
            return f"too old ({age_s:.0f} s, replay attack)"
        self.seen_cmd_ids.append(cmd.get("cmd_id"))
        return None

    def reject_command(self, cmd, problem):
        self.rejected += 1
        self.say(f"[SECURITY] REJECTED command {cmd.get('action')} {cmd.get('target') or ''}: {problem}")
        if time.time() - self.last_security_alert > 5:  # don't flood the broker during an attack
            self.last_security_alert = time.time()
            self.client.publish(self.topics["alert"], json.dumps({
                "ts": now_ms(), "source": "iot-device", "level": "CRITICAL", "type": "FORGED_COMMAND",
                "message": f"IoT device rejected a command ({cmd.get('action')} {cmd.get('target') or ''}): "
                           f"{problem}. Rejected so far: {self.rejected}"}), qos=1)

    def show_prediction(self, p):
        """Print the cloud's verdict when it changes (it arrives for every reading)."""
        self.last_cloud_msg = time.time()
        if p.get("state") != self.cloud_state:
            self.cloud_state = p.get("state")
            self.say(f"[CLOUD]   system state = {self.cloud_state}   (model {p.get('model')}, "
                     f"confidence {100 * p.get('confidence', 0):.0f}%, actual {p.get('gt_name')})")

    def execute_command(self, cmd):
        """Actuator: apply one control command and acknowledge it."""
        action, target, value = cmd.get("action"), cmd.get("target"), cmd.get("value")
        effect = None
        with self.lock:
            s = self.state
            if action == "set_pump" and target in s["pumps"]:
                s["pumps"][target] = float(value)
            elif action in ("isolate_zone", "close_valve"):
                valve = ZONE_VALVES.get(target) if action == "isolate_zone" else target
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
            elif action != "alert_only":
                self.say(f"[COMMAND] unknown command ignored: {cmd}")
                return
            if effect:
                self.pending_skip = (time.time() + self.cfg["device"]["valve_travel_s"], effect)
            closed = [v for v, st in s["valves"].items() if st == "CLOSED"]
            self.say(f"[ACTUATOR] done. mode={s['mode']} pump1={s['pumps']['pump1']:.0f}% "
                     f"closed valves={closed or 'none'} flagged={s['flagged_sensors'] or 'none'}")
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
        if self.rows is None:
            return
        msg = {"device_id": DEVICE_ID, "online": True, "scenario": self.scenario, "segment": self.segment,
               "row": self.row, "end": self.end, "rate_hz": self.rate_hz, "paused": self.paused,
               "random_mode": self.random_mode,
               "ts": now_ms()}
        self.client.publish(self.topics["sim_status"], json.dumps(msg), qos=1, retain=True)

    def sim_control(self, c):
        """Requests from the cloud dashboard: inject a scenario, change speed, pause."""
        action = c.get("action")
        with self.lock:
            if action == "play" and os.path.exists(os.path.join(HERE, "recordings", f"{c.get('scenario')}.csv")):
                self.start_item({"scenario": c["scenario"], "start": c.get("start"), "end": c.get("end")})
            elif action == "rate":
                self.rate_hz = max(0.5, min(50.0, float(c.get("value", self.rate_hz))))
                self.say(f"[DATA]    speed set to {self.rate_hz} readings/s by the cloud")
            elif action == "pause":
                self.paused = True
                self.say("[DATA]    paused by the cloud")
            elif action == "resume":
                self.paused = False
                self.say("[DATA]    resumed by the cloud")
            elif action == "random":
                self.random_mode = c.get("value") in (True, 1, "on", "ON")
                self.say(f"[DATA]    random events {'ON' if self.random_mode else 'OFF (back to the playlist)'}")
                self.next_item()
            elif action == "next":
                self.next_item()
        self.publish_status()

    # ------------------------------------------------------------------ main loop
    def publish_reading(self):
        r = self.rows[self.row]
        gt = int(r["gt_class"])
        msg = {"device_id": DEVICE_ID, "seq": self.seq, "ts": now_ms(), "scenario": self.scenario,
               "segment": self.segment, "row": self.row, **{c: r[c] for c in SENSORS},
               "gt_class": gt, "gt_name": CLASS_NAMES[gt]}
        self.client.publish(self.topics["telemetry"], json.dumps(msg), qos=0)
        self.seq += 1

    def show_progress(self):
        if not self.client.is_connected():
            print("          waiting for the broker - check the internet connection ...", flush=True)
            return
        r = self.rows[min(self.row, self.end - 1)]
        cloud = "cloud is responding" if time.time() - self.last_cloud_msg < 5 else "NO REPLY FROM CLOUD YET"
        print(f"          sent {self.seq} readings | {self.scenario} {self.row}/{self.end} | "
              f"P1={r['p1_out']:.2f} P2={r['p2_out']:.2f} F1={r['f1']:.2f} | {cloud}", flush=True)

    def run(self):
        host, port = self.cfg["mqtt"]["host"], self.cfg["mqtt"]["port"]
        print(f"Smart Water Distribution - IoT device\nBroker {host}:{port}  project {self.cfg['project_id']}\n"
              f"Press Ctrl+C to stop.\n", flush=True)
        self.client.connect_async(host, port, keepalive=30)
        self.client.loop_start()
        self.next_item()
        next_t = time.time()
        last_status = last_progress = 0
        try:
            while True:
                with self.lock:
                    if self.pending_skip and time.time() >= self.pending_skip[0]:
                        reason = self.pending_skip[1]
                        self.pending_skip = None
                        self.skip_anomaly(reason)
                    if self.row >= self.end:
                        self.next_item()
                    if not self.paused and self.client.is_connected():
                        self.publish_reading()
                        self.row += 1
                    period = 1.0 / self.rate_hz
                if time.time() - last_status > 2:
                    self.publish_status()
                    last_status = time.time()
                if time.time() - last_progress > 5:
                    self.show_progress()
                    last_progress = time.time()
                next_t += period
                time.sleep(max(0.0, next_t - time.time()))
                if time.time() - next_t > 1:  # fell behind (e.g. laptop asleep)
                    next_t = time.time()
        except KeyboardInterrupt:
            print("\nStopping ...")
        finally:
            info = self.client.publish(self.topics["sim_status"],
                                       json.dumps({"device_id": DEVICE_ID, "online": False}), qos=1, retain=True)
            try:
                info.wait_for_publish(2)
            except (RuntimeError, ValueError):
                pass
            self.client.loop_stop()
            self.client.disconnect()


def main():
    with open(os.path.join(HERE, "device_config.json"), encoding="utf-8") as f:
        cfg = json.load(f)
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default=cfg["mqtt"]["host"], help="MQTT broker address")
    ap.add_argument("--port", type=int, default=cfg["mqtt"]["port"])
    ap.add_argument("--rate", type=float, default=cfg["device"]["rate_hz"], help="readings per second")
    ap.add_argument("--scenario", help="loop a single recording instead of the playlist")
    ap.add_argument("--random", action="store_true", help="random events with normal data in between")
    args = ap.parse_args()
    cfg["mqtt"].update(host=args.host, port=args.port)
    playlist = [{"scenario": args.scenario}] if args.scenario else cfg["device"]["playlist"]
    IoTDevice(cfg, playlist, args.rate, random_mode=args.random).run()


if __name__ == "__main__":
    main()
