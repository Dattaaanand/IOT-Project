"""Analytics + Decision Engine (arrows 4, 5 and 7 of the architecture).

    python -m engine.decision_engine
    python -m engine.decision_engine --model cnn_lstm --no-auto

1. Reads every new reading that Node-RED stored in SQLite (arrow 4).
2. Classifies it with the selected ML model -> NORMAL / LEAK / SENSOR_FAULT /
   CYBER_ATTACK, combined with a physical-range rule for impossible readings.
3. Confirms an event only after `votes_required` of the last `vote_window`
   predictions agree (prevents switching on single noisy readings).
4. Works out leak severity (minor / moderate / severe) and the suspected zone,
   or the faulty sensor, from deviations against a baseline of normal readings.
5. Raises alerts and publishes control commands over MQTT (arrow 7):
     LEAK          reduce pump speed; isolate the suspected zone if moderate/severe
     SENSOR_FAULT  flag the sensor for maintenance (no hydraulic action)
     CYBER_ATTACK  switch the field controller to SAFE mode
   and restores normal operation once the network has been normal for a while.
Runtime settings can be changed from the dashboard via `wds/engine/config`.
"""
import argparse
import json
import threading
import time
from collections import Counter, deque

import numpy as np
import pandas as pd
import paho.mqtt.client as mqtt

from common import db
from common.config import (CLASS_NAMES, CYBER_ATTACK, LEAK, NORMAL, PRESSURES, FLOWS, SENSOR_FAULT, SENSORS,
                           ZONES, abspath, load_config)
from common.features import invalid_sensors, leak_index, severity_from_index, zone_leak_scores
from common.predictor import Predictor, load_meta
from engine.kpi import KPITracker

SEVERITY_RANK = {"minor": 0, "moderate": 1, "severe": 2}
KPI_FILE = abspath("ml/results/closed_loop_kpis.json")


def now_ms():
    return int(time.time() * 1000)


class DecisionEngine:
    def __init__(self, cfg, model_name=None, auto_mode=None):
        self.cfg = cfg
        self.topics = cfg["topics"]
        self.d = dict(cfg["decision"])
        if auto_mode is not None:
            self.d["auto_mode"] = auto_mode
        self.meta = load_meta()
        self.thresholds = self.meta["severity_thresholds"]
        name = model_name or cfg["ml"]["active_model"]
        self.predictor = Predictor(self.meta["best_model"] if name == "auto" else name, meta=self.meta)
        self.lock = threading.Lock()

        self.window = deque(maxlen=self.predictor.history_needed)
        self.baseline_rows = deque(maxlen=120)
        self.segment = None
        self.votes = deque(maxlen=self.d["vote_window"])
        self.event = None            # active (confirmed) event
        self.event_seq = 0
        self.normal_streak = 0
        self.ineff_streak = 0
        self.last_alert = {}
        self.cmd_seq = 0
        self.pending = {}            # cmd_id -> command info, waiting for ack
        self.kpi = KPITracker()

        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="wds-decision-engine")
        self.client.on_connect = self.on_connect
        self.client.on_message = self.on_message
        self.client.reconnect_delay_set(1, 10)

    # ------------------------------------------------------------------ MQTT in
    def on_connect(self, client, userdata, flags, reason_code, properties):
        print(f"[engine] connected to broker ({reason_code})")
        client.subscribe([(self.topics["actuator_state"], 1), (self.topics["engine_config"], 1)])
        self.publish_config()

    def on_message(self, client, userdata, msg):
        try:
            payload = json.loads(msg.payload.decode())
        except ValueError:
            return
        with self.lock:
            if msg.topic == self.topics["actuator_state"]:
                self.handle_ack(payload)
            elif msg.topic == self.topics["engine_config"]:
                self.handle_config(payload)

    def handle_ack(self, ack):
        if ack.get("type") != "ack":
            return
        if ack.get("action") == "process_effect" and ack.get("effect") == "leak_isolated" and self.event:
            # volume that would have kept leaking = excess supply flow x skipped readings
            excess = max(0.0, self.event.get("excess_flow", 0.0))
            self.kpi.add_water_saved(excess * float(ack.get("value") or 0))
        pending = self.pending.pop(ack.get("cmd_id"), None)
        self.kpi.on_ack(ack, pending)
        if pending:
            print(f"[engine] ack {pending['action']} {pending.get('target') or ''} "
                  f"in {ack['ack_ts'] - pending['ts']} ms")

    def handle_config(self, c):
        if "active_model" in c and c["active_model"] != self.predictor.name:
            try:
                self.predictor = Predictor(c["active_model"], meta=self.meta)
                print(f"[engine] switched model -> {self.predictor.name}")
                self.alert("INFO", "MODEL_SWITCH", f"Active model changed to {self.predictor.name}", force=True)
            except KeyError as e:
                print(f"[engine] {e}")
        for key in ("auto_mode", "min_confidence", "votes_required", "vote_window", "normal_to_restore"):
            if key in c:
                self.d[key] = type(self.d[key])(c[key])
                print(f"[engine] {key} = {self.d[key]}")
        if "vote_window" in c:
            self.votes = deque(self.votes, maxlen=self.d["vote_window"])
        if c.get("reset_kpis"):
            self.kpi.reset()
            print("[engine] KPIs reset")
        self.publish_config()

    # ------------------------------------------------------------------ MQTT out
    def publish(self, topic, payload, retain=False):
        self.client.publish(topic, json.dumps(payload, default=float), qos=1, retain=retain)

    def publish_config(self):
        self.publish(self.topics["engine_config"] + "/current", {
            "active_model": self.predictor.name, "family": self.predictor.family,
            "best_model": self.meta["best_model"], "available_models": list(self.meta["models"]),
            **{k: self.d[k] for k in ("auto_mode", "min_confidence", "votes_required", "vote_window",
                                      "normal_to_restore")}}, retain=True)

    def alert(self, level, typ, message, force=False, **extra):
        key = (typ, extra.get("zone"), extra.get("sensor"))
        if not force and time.time() - self.last_alert.get(key, 0) < self.d["alert_cooldown_s"]:
            return
        self.last_alert[key] = time.time()
        print(f"[engine] ALERT {level} {typ}: {message}")
        self.publish(self.topics["alert"], {"ts": now_ms(), "source": "engine", "level": level, "type": typ,
                                            "message": message, **extra})

    def command(self, action, target=None, value=None, reason="", row=None):
        """Send a control command (arrow 7). In manual mode it is only recommended."""
        if not self.d["auto_mode"]:
            self.alert("INFO", "RECOMMENDATION", f"Recommended action: {action} {target or ''} {value or ''} "
                                                 f"({reason}) - auto mode is OFF", force=True)
            return
        self.cmd_seq += 1
        cmd = {"cmd_id": f"eng-{int(time.time())}-{self.cmd_seq}", "ts": now_ms(), "source": "engine",
               "action": action, "target": target, "value": value, "reason": reason,
               "event_id": self.event["id"] if self.event else None}
        first = bool(self.event) and not self.event["actions"]
        self.pending[cmd["cmd_id"]] = {**cmd, "first_of_event": first,
                                       "trigger_ts": self.event["trigger_ts"] if self.event else cmd["ts"],
                                       "gt_onset_ts": self.event.get("gt_onset_ts") if self.event else None}
        if self.event is not None:
            self.event["actions"].append(action)
        self.kpi.on_command(action, int(row["gt_class"]) if row is not None else NORMAL)
        print(f"[engine] CMD {action} {target or ''} {'' if value is None else value}  ({reason})")
        self.publish(self.topics["command"], cmd)

    # ------------------------------------------------------------------ analysis
    def baseline(self):
        rows = self.baseline_rows if len(self.baseline_rows) >= 10 else list(self.window)[:20]
        return pd.DataFrame(list(rows))[SENSORS].median()

    def faulty_sensor(self, row, invalid):
        if invalid:
            return invalid[0]
        base = self.baseline()
        dev = {c: abs(row[c] - base[c]) / (abs(base[c]) + 0.1) for c in PRESSURES + FLOWS}
        return max(dev, key=dev.get)

    def classify(self, row):
        """ML prediction + physical rule (impossible readings are sensor faults)."""
        t0 = time.perf_counter()
        df = pd.DataFrame(list(self.window))
        probs = self.predictor.predict_proba(df)[0]
        inference_ms = 1000 * (time.perf_counter() - t0)
        pred, conf = int(np.argmax(probs)), float(np.max(probs))
        invalid = invalid_sensors(row, self.d["pressure_valid_range"], self.d["flow_valid_range"])
        source = "ml"
        if invalid and pred != SENSOR_FAULT:
            pred, conf, source = SENSOR_FAULT, 0.99, "rule"
        return pred, conf, probs, invalid, source, inference_ms

    def confirmed_class(self):
        counts = Counter(c for c, conf in self.votes if conf >= self.d["min_confidence"] and c != NORMAL)
        if counts:
            cls, n = counts.most_common(1)[0]
            if n >= self.d["votes_required"]:
                return cls
        return None

    # ------------------------------------------------------------------ decisions
    def start_event(self, cls, row, invalid):
        if self.event:
            self.close_event("superseded")
        self.event_seq += 1
        self.event = {"id": self.event_seq, "class": cls, "start_ts": now_ms(), "trigger_ts": row["ts"],
                      "severity": None, "zone": None, "sensor": None, "actions": []}
        self.kpi.on_event_confirmed(cls, row, self.event)
        name = CLASS_NAMES[cls]
        if cls == LEAK:
            self.update_leak(row, initial=True)
        elif cls == SENSOR_FAULT:
            sensor = self.faulty_sensor(row, invalid)
            self.event["sensor"] = sensor
            self.alert("WARNING", name, f"Sensor fault detected on {sensor} (reading {row[sensor]:.3f}). "
                                        f"Sensor flagged; readings not trusted for control.",
                       force=True, sensor=sensor)
            self.command("flag_sensor", sensor, reason="sensor fault", row=row)
        elif cls == CYBER_ATTACK:
            self.alert("CRITICAL", name, "Abnormal actuator/sensor pattern consistent with a cyber-attack "
                                         "(valve/pump manipulation). Switching to SAFE mode.", force=True)
            self.command("safe_mode", value=True, reason="cyber-attack suspected", row=row)

    def update_leak(self, row, initial=False):
        """Compute severity + zone; escalate actions if the leak gets worse."""
        base = self.baseline()
        recent = pd.DataFrame(list(self.window)[-5:])[SENSORS].mean()
        idx = leak_index(recent, base)
        scores = zone_leak_scores(recent, base)
        zone = max(scores, key=scores.get)
        sev = severity_from_index(idx, self.thresholds)
        ev = self.event
        ev["leak_index"] = idx
        ev["excess_flow"] = float(recent["f1"] - base["f1"])
        if not initial and SEVERITY_RANK[sev] <= SEVERITY_RANK[ev["severity"]]:
            return
        escalated = not initial
        ev["severity"], ev["zone"] = sev, ev["zone"] or zone
        level = "WARNING" if sev == "minor" else "CRITICAL"
        self.alert(level, "LEAK", f"{'Leak escalated to' if escalated else 'Leak detected:'} {sev.upper()} "
                                  f"(leak index {idx:.2f}), suspected zone {ev['zone']}",
                   force=True, severity=sev, zone=ev["zone"])
        self.command("set_pump", "pump1", self.d["pump_reduction"][sev],
                     reason=f"{sev} leak - reduce pressure", row=row)
        if SEVERITY_RANK[sev] >= SEVERITY_RANK[self.d["isolate_from_severity"]] and "isolate_zone" not in ev["actions"]:
            self.command("isolate_zone", ev["zone"], reason=f"{sev} leak in zone {ev['zone']}", row=row)

    def close_event(self, why):
        ev = self.event
        self.event = None
        if ev is None:
            return
        dur = (now_ms() - ev["start_ts"]) / 1000
        print(f"[engine] event #{ev['id']} {CLASS_NAMES[ev['class']]} closed ({why}) after {dur:.1f}s")
        return ev

    def resolve(self, row):
        ev = self.close_event("resolved")
        self.alert("INFO", "RESOLVED", f"{CLASS_NAMES[ev['class']]} cleared - network back to normal "
                                       f"for {self.d['normal_to_restore']} readings", force=True)
        if ev["actions"]:
            self.command("restore", reason="normal operation restored", row=row)

    def check_pump_efficiency(self, row):
        """Advisory: pump 1 needs much more speed per unit of flow than usual."""
        if len(self.baseline_rows) < 60:
            return
        base = self.baseline()
        ratio = (row["vfd1"] / (row["f1"] + 0.05)) / (base["vfd1"] / (base["f1"] + 0.05) + 1e-6)
        self.ineff_streak = self.ineff_streak + 1 if ratio > self.d["pump_inefficiency_ratio"] else 0
        if self.ineff_streak == 20:
            self.alert("WARNING", "PUMP_INEFFICIENT", f"Pump 1 speed/flow ratio is {ratio:.1f}x the normal value "
                                                      f"- check pump wear or a closed valve")

    # ------------------------------------------------------------------ main step
    def process(self, row):
        if row["segment"] != self.segment:
            # replay jumped to another recording: start a fresh history
            self.segment = row["segment"]
            self.window.clear()
            self.baseline_rows.clear()
            self.votes.clear()
        self.window.append(row)
        self.kpi.track_ground_truth(row)
        pred, conf, probs, invalid, source, inference_ms = self.classify(row)
        self.votes.append((pred, conf))

        confirmed = self.confirmed_class()
        if confirmed is not None and (self.event is None or confirmed != self.event["class"]):
            self.start_event(confirmed, row, invalid)
            self.normal_streak = 0
        elif self.event is not None:
            self.normal_streak = self.normal_streak + 1 if pred == NORMAL else 0
            if self.event["class"] == LEAK:
                self.update_leak(row)
            if self.normal_streak >= self.d["normal_to_restore"]:
                self.resolve(row)
        elif pred == NORMAL:
            self.baseline_rows.append(row)
            self.check_pump_efficiency(row)

        state_cls = self.event["class"] if self.event else NORMAL
        pipeline_ms = now_ms() - row["ts"]
        self.kpi.on_reading(row, pred, state_cls, inference_ms, pipeline_ms)
        ev = self.event or {}
        self.publish(self.topics["prediction"], {
            "ts": now_ms(), "telemetry_id": row["id"], "telemetry_ts": row["ts"], "model": self.predictor.name,
            "pred_class": pred, "pred_name": CLASS_NAMES[pred], "confidence": round(conf, 4), "source": source,
            "probs": {n: round(float(p), 4) for n, p in zip(CLASS_NAMES, probs)},
            "state": CLASS_NAMES[state_cls], "severity": ev.get("severity"), "zone": ev.get("zone"),
            "sensor": ev.get("sensor"), "leak_index": round(ev.get("leak_index", 0.0), 4),
            "gt_class": row["gt_class"], "gt_name": CLASS_NAMES[int(row["gt_class"])],
            "latency_ms": round(inference_ms, 2), "pipeline_ms": pipeline_ms})

    def publish_kpis(self):
        info = {"active_model": self.predictor.name, "auto_mode": self.d["auto_mode"],
                "state": CLASS_NAMES[self.event["class"]] if self.event else "NORMAL"}
        summary = self.kpi.summary(info)
        self.publish(self.topics["kpi"], summary)
        return summary

    def run(self, poll_s=0.05):
        db.init_db()
        conn = db.connect()
        last_id = conn.execute("SELECT COALESCE(MAX(id), 0) FROM telemetry").fetchone()[0]
        host, port = self.cfg["mqtt"]["host"], self.cfg["mqtt"]["port"]
        self.client.connect(host, port, keepalive=30)
        self.client.loop_start()
        print(f"[engine] model={self.predictor.name} ({self.predictor.family}), auto_mode={self.d['auto_mode']}, "
              f"waiting for telemetry after id {last_id}")
        last_kpi = last_save = time.time()
        try:
            while True:
                rows = conn.execute("SELECT * FROM telemetry WHERE id > ? ORDER BY id LIMIT 500",
                                    (last_id,)).fetchall()
                with self.lock:
                    for r in rows:
                        self.process(dict(r))
                        last_id = r["id"]
                    if time.time() - last_kpi >= 2:
                        summary = self.publish_kpis()
                        last_kpi = time.time()
                        if time.time() - last_save >= 10:
                            with open(KPI_FILE, "w") as f:
                                json.dump(summary, f, indent=2)
                            last_save = time.time()
                if not rows:
                    time.sleep(poll_s)
        except KeyboardInterrupt:
            pass
        finally:
            with open(KPI_FILE, "w") as f:
                json.dump(self.kpi.summary({"active_model": self.predictor.name}), f, indent=2)
            self.client.loop_stop()
            self.client.disconnect()
            print(f"[engine] stopped, KPIs saved to {KPI_FILE}")


def main():
    cfg = load_config()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--model", help="model name from ml/models/meta.json ('auto' = best)")
    ap.add_argument("--no-auto", action="store_true", help="alerts only, no control commands")
    ap.add_argument("--host", default=cfg["mqtt"]["host"])
    ap.add_argument("--port", type=int, default=cfg["mqtt"]["port"])
    args = ap.parse_args()
    cfg["mqtt"].update(host=args.host, port=args.port)
    DecisionEngine(cfg, args.model, False if args.no_auto else None).run()


if __name__ == "__main__":
    main()
