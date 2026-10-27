"""Project-wide constants and config loading.

Every component (simulator, trainer, decision engine) imports from here so that
sensor names, class labels and MQTT topics are defined in exactly one place.
"""
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(ROOT, "config.json")

# Column names in the WDSEventDB Excel files -> short keys used on MQTT / in SQL
COLUMN_MAP = {
    "Pressure 1 Out": "p1_out",
    "Pressure 2 Out": "p2_out",
    "Pressure 3 In": "p3_in",
    "Pressure 4 In": "p4_in",
    "Water Flow 1": "f1",
    "Water Flow 2": "f2",
    "Water Flow 3": "f3",
    "Water Flow 4": "f4",
    "VFD 1": "vfd1",
    "VFD 2": "vfd2",
    "VFD 3": "vfd3",
    "VFD 4-1": "vfd4_1",
    "VFD 4-2": "vfd4_2",
    "Analog Valve 1": "valve1",
    "Analog Valve 2": "valve2",
}
SENSORS = list(COLUMN_MAP.values())
PRESSURES = ["p1_out", "p2_out", "p3_in", "p4_in"]
FLOWS = ["f1", "f2", "f3", "f4"]
PUMPS = ["vfd1", "vfd2", "vfd3", "vfd4_1", "vfd4_2"]
VALVES = ["valve1", "valve2"]

# Network sections (zones). Each zone is monitored by one pressure + one flow
# sensor; zone A is the main supply line fed by pump 1.
ZONES = {
    "A": {"pressure": "p1_out", "flow": "f1", "valve": "iso_A"},
    "B": {"pressure": "p2_out", "flow": "f2", "valve": "iso_B"},
    "C": {"pressure": "p3_in", "flow": "f3", "valve": "iso_C"},
    "D": {"pressure": "p4_in", "flow": "f4", "valve": "iso_D"},
}

# Multi-class target. The dataset's binary "Labels" column is mapped to a class
# using the folder the file came from.
CLASS_NAMES = ["NORMAL", "LEAK", "SENSOR_FAULT", "CYBER_ATTACK"]
NORMAL, LEAK, SENSOR_FAULT, CYBER_ATTACK = range(4)

# scenario id -> (relative path inside dataset_dir, class used when Labels == 1)
SCENARIOS = {
    "clean": ("Clean/CleanData.xlsx", NORMAL),
    "leak1": ("Leakage/LeakEvent1.xlsx", LEAK),
    "leak2": ("Leakage/LeakEvent2.xlsx", LEAK),
    "leak3": ("Leakage/LeakEvent3.xlsx", LEAK),
    "sensor1": ("Sensor Failure/SensorEvent1.xlsx", SENSOR_FAULT),
    "sensor23": ("Sensor Failure/SensorEvent23.xlsx", SENSOR_FAULT),
    "sensor45": ("Sensor Failure/SensorEvent45.xlsx", SENSOR_FAULT),
    "sensor67": ("Sensor Failure/SensorEvent67.xlsx", SENSOR_FAULT),
    "cyber": ("Cyberattack/CyberEvent1-4.xlsx", CYBER_ATTACK),
}


def load_config(path=CONFIG_PATH):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def abspath(rel):
    """Resolve a path from config.json relative to the project root."""
    return rel if os.path.isabs(rel) else os.path.join(ROOT, rel)
