"""Closed-loop performance tracking (uses the replayed ground truth only for scoring).

* ML online      : accuracy / per-class recall on the live stream
* Detection      : delay from true event onset to confirmed detection, missed events
* Response       : command -> actuator acknowledgement, and onset -> first action
* End-to-end     : sensor timestamp of the triggering reading -> actuator ack
                   (MQTT -> Node-RED -> SQLite -> ML -> decision -> MQTT -> actuator)
* Stability      : actions issued while the network was actually normal,
                   valve open/close cycles, false events
* Resource       : engine CPU/memory, inference latency, water saved by isolation
"""
import time
from collections import Counter

import numpy as np
import psutil

from common.config import CLASS_NAMES, NORMAL


def _stats(values):
    if not values:
        return None
    a = np.asarray(values, dtype=float)
    return {"mean": round(float(a.mean()), 1), "min": round(float(a.min()), 1),
            "max": round(float(a.max()), 1), "n": int(len(a))}


class KPITracker:
    def __init__(self):
        self.reset()
        self.proc = psutil.Process()
        self.proc.cpu_percent(None)

    def reset(self):
        self.started = time.time()
        self.confusion = Counter()        # (gt, pred) -> count
        self.state_confusion = Counter()  # (gt, confirmed state) -> count
        self.gt_events = []               # true events seen in the stream
        self.open_gt = None
        self.prev_gt = (None, NORMAL)     # (segment, class)
        self.detections = []              # confirmed engine events
        self.detection_delay_ms, self.detection_delay_samples = [], []
        self.response_ms, self.onset_to_action_ms, self.end_to_end_ms = [], [], []
        self.inference_ms, self.pipeline_ms = [], []
        self.actions_total = 0
        self.actions_while_normal = 0
        self.manual_actions = 0
        self.valve_cycles = 0
        self.false_events = 0
        self.wrong_class_events = 0
        self.water_saved = 0.0
        self.effects = Counter()

    # ---- per reading -------------------------------------------------------
    def track_ground_truth(self, row):
        """Called first for every reading: notes where true events start/end."""
        gt, seg = int(row["gt_class"]), row["segment"]
        prev_seg, prev_gt = self.prev_gt
        if gt != prev_gt or seg != prev_seg:
            if self.open_gt is not None:
                self.open_gt["end_ts"] = row["ts"]
                self.open_gt = None
            if gt != NORMAL:
                self.open_gt = {"class": gt, "onset_ts": row["ts"], "onset_id": row["id"],
                                "detected": False, "end_ts": None}
                self.gt_events.append(self.open_gt)
        self.prev_gt = (seg, gt)

    def on_reading(self, row, pred, state_cls, inference_ms, pipeline_ms):
        gt = int(row["gt_class"])
        self.confusion[(gt, pred)] += 1
        self.state_confusion[(gt, state_cls)] += 1
        self.inference_ms.append(inference_ms)
        self.pipeline_ms.append(pipeline_ms)
        self.inference_ms, self.pipeline_ms = self.inference_ms[-2000:], self.pipeline_ms[-2000:]

    # ---- per confirmed event ---------------------------------------------
    def on_event_confirmed(self, cls, row, event):
        self.detections.append(event)
        gt = self.open_gt
        if gt is None:
            self.false_events += 1
            event["kpi_match"] = "false_alarm"
            return
        if gt["class"] != cls:
            self.wrong_class_events += 1
            event["kpi_match"] = "wrong_class"
        else:
            event["kpi_match"] = "correct"
        if not gt["detected"]:
            gt["detected"] = True
            event["gt_onset_ts"] = gt["onset_ts"]
            self.detection_delay_ms.append(time.time() * 1000 - gt["onset_ts"])
            self.detection_delay_samples.append(row["id"] - gt["onset_id"] + 1)

    def on_command(self, action, gt_cls):
        self.actions_total += 1
        # a corrective action while the network is actually normal = unnecessary
        # switching ("restore" is expected to happen during normal operation)
        if gt_cls == NORMAL and action != "restore":
            self.actions_while_normal += 1

    def on_ack(self, ack, pending):
        """`pending` is the engine's record of the command (None for manual ones)."""
        if ack.get("action") == "process_effect":
            self.effects[ack.get("effect")] += 1
            return
        if ack.get("source") != "engine":
            self.manual_actions += 1
        if ack.get("action") in ("isolate_zone", "close_valve"):
            self.valve_cycles += 1
        if pending is None:
            return
        ack_ts = ack["ack_ts"]
        self.response_ms.append(ack_ts - pending["ts"])
        if pending.get("first_of_event"):
            self.end_to_end_ms.append(ack_ts - pending["trigger_ts"])
            if pending.get("gt_onset_ts"):
                self.onset_to_action_ms.append(ack_ts - pending["gt_onset_ts"])

    def add_water_saved(self, amount):
        self.water_saved += amount

    # ---- summary ---------------------------------------------------------
    def summary(self, engine_info):
        total = sum(self.confusion.values())
        correct = sum(v for (g, p), v in self.confusion.items() if g == p)
        recall = {}
        for c, name in enumerate(CLASS_NAMES):
            n = sum(v for (g, _), v in self.confusion.items() if g == c)
            if n:
                recall[name] = round(self.confusion[(c, c)] / n, 4)
        normal_n = sum(v for (g, _), v in self.state_confusion.items() if g == NORMAL)
        normal_alarm = sum(v for (g, s), v in self.state_confusion.items() if g == NORMAL and s != NORMAL)
        finished = [e for e in self.gt_events if e["end_ts"] is not None]
        return {
            "ts": int(time.time() * 1000),
            "uptime_s": round(time.time() - self.started, 1),
            "readings": total,
            "online_accuracy": round(correct / total, 4) if total else None,
            "online_recall": recall,
            "false_alarm_rate": round(normal_alarm / normal_n, 4) if normal_n else None,
            "true_events": len(self.gt_events),
            "detected_events": sum(e["detected"] for e in self.gt_events),
            "missed_events": sum(not e["detected"] for e in finished),
            "false_events": self.false_events,
            "wrong_class_events": self.wrong_class_events,
            "detection_delay_ms": _stats(self.detection_delay_ms),
            "detection_delay_samples": _stats(self.detection_delay_samples),
            "response_time_ms": _stats(self.response_ms),
            "onset_to_action_ms": _stats(self.onset_to_action_ms),
            "end_to_end_latency_ms": _stats(self.end_to_end_ms),
            "inference_ms": _stats(self.inference_ms[-500:]),
            "pipeline_latency_ms": _stats(self.pipeline_ms[-500:]),
            "actions_total": self.actions_total,
            "actions_while_normal": self.actions_while_normal,
            "manual_actions": self.manual_actions,
            "valve_cycles": self.valve_cycles,
            "process_effects": dict(self.effects),
            "water_saved": round(self.water_saved, 2),
            "engine_cpu_percent": round(self.proc.cpu_percent(None), 1),
            "engine_memory_mb": round(self.proc.memory_info().rss / 2 ** 20, 1),
            **engine_info,
        }
