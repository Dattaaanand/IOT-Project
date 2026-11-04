"""Builds wds_iot_device.zip, the package for the IoT device laptop.

    python tools/build_device_zip.py

Takes the broker and topics from config.json so that both laptops always match,
exports the recordings to CSV and bundles paho-mqtt (pure Python), so the device
laptop needs nothing but Python itself. Rebuild after changing the broker or topics.
"""
import json
import os
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import paho  # noqa: E402

from common.config import SCENARIOS, SENSORS, load_config  # noqa: E402
from common.dataset import load_scenario  # noqa: E402

SOURCE = os.path.join(ROOT, "iot_device")
OUT = os.path.join(ROOT, "wds_iot_device.zip")
FOLDER = "wds_iot_device"
DEVICE_TOPICS = ["telemetry", "prediction", "alert", "command", "actuator_state",
                 "sim_control", "sim_status", "ping", "pong"]


def main():
    cfg = load_config()
    with open(os.path.join(SOURCE, "device_settings.json"), encoding="utf-8") as f:
        settings = json.load(f)
    device_cfg = {"project_id": cfg["project_id"], "mqtt": cfg["mqtt"],
                  "topics": {k: cfg["topics"][k] for k in DEVICE_TOPICS}, "device": settings}

    with zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED) as z:
        for name in ["iot_device.py", "README.txt", "run_device.bat", "run_device.sh"]:
            z.write(os.path.join(SOURCE, name), f"{FOLDER}/{name}")
        z.writestr(f"{FOLDER}/device_config.json", json.dumps(device_cfg, indent=2))
        for name in SCENARIOS:
            df = load_scenario(name, cfg["dataset_dir"])
            z.writestr(f"{FOLDER}/recordings/{name}.csv", df[SENSORS + ["gt_class"]].to_csv(index=False))
        paho_dir = list(paho.__path__)[0]
        for base, _, files in os.walk(paho_dir):
            for file in files:
                if file.endswith(".py"):
                    path = os.path.join(base, file)
                    z.write(path, f"{FOLDER}/vendor/paho/{os.path.relpath(path, paho_dir)}")
    print(f"{OUT}  ({os.path.getsize(OUT) / 2 ** 20:.1f} MB), broker {cfg['mqtt']['host']}, project {cfg['project_id']}")


if __name__ == "__main__":
    main()
