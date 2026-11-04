# Smart Water Distribution and Leakage Detection (Team 2)

IoT mini-project, B.Tech CSE Sem 7. It uses the real **WDSEventDB** water-distribution testbed dataset and follows the given architecture end to end, on **two laptops connected through the online HiveMQ broker**.

```
 DEVICE LAPTOP (wds_iot_device.zip)            CLOUD LAPTOP (this folder)
 ┌────────────────────┐      ┌─────────┐      ┌───────────────────────────────┐
 │ sensors: replay    │─①───►│         │─②───►│ Node-RED ─③─► SQLite, Dashboard│
 │ the dataset        │      │ HiveMQ  │      │                 │④            │
 │ actuators: pumps,  │◄─⑧───│ (online)│◄─⑦───│ Decision engine ◄⑤─ ML models │
 │ valves, safe mode  │      └─────────┘      └───────────────────────────────┘
 └────────────────────┘
```

| Arrow | What happens | Where |
|---|---|---|
| ① | Sensor readings (4 pressure, 4 flow, 5 pump VFD, 2 valve) published as JSON on `wds-t2-906e031a/sensors/telemetry` | device: `iot_device.py` |
| ② | Node-RED subscribes, validates (range check, edge alert) and enriches each reading | flow tab *1-3 Ingest* |
| ③ | Readings stored in SQLite (`telemetry` table) and sent to the Node-RED Dashboard | flow tabs *Ingest* and *Dashboard* |
| ④ | The decision engine reads every new row from SQLite | `engine/decision_engine.py` |
| ⑤ | ML model classifies NORMAL / LEAK / SENSOR_FAULT / CYBER_ATTACK; the decision logic confirms the event and works out severity and zone | `engine/`, `common/predictor.py` |
| ⑦ | Control commands published on `wds-t2-906e031a/control/cmd` | decision engine, dashboard *Control Panel* |
| ⑧ | Device actuators (pumps, isolation valves, safe mode) execute the command and ack on `wds-t2-906e031a/actuators/state` | device: `iot_device.py` |

---

## 1. Dataset (WDSEventDB)

The dataset has 15 signals per reading: `Pressure 1 Out`, `Pressure 2 Out`, `Pressure 3 In`, `Pressure 4 In`, `Water Flow 1-4`, `VFD 1, 2, 3, 4-1, 4-2` (pump drives) and `Analog Valve 1-2`. The `Labels` column is 0 = normal, 1 = event.

| Folder | Files | Event |
|---|---|---|
| Clean | CleanData.xlsx (9,743 readings) | none |
| Leakage | LeakEvent1-3 | leak (label 1 from the leak onset) |
| Sensor Failure | SensorEvent1, 23, 45, 67 | SensorEvent1: P1; 23: P2 (large), then a small P4 fault; 45: P3 twice; 67: P4 twice |
| Cyberattack | CyberEvent1-4 | 4 attack windows (valve 2 forced to ~100 %, pump 1 reduced) |

The binary label is combined with the folder name to make a **4-class** target:
`0 NORMAL, 1 LEAK, 2 SENSOR_FAULT, 3 CYBER_ATTACK`.

> The dataset has no tank-level sensor, so tank level is not used. Pump status comes from the VFD signals.
> Note: the inlet pressures P3/P4 legitimately go down to about −2.8 bar in normal operation (suction side), so the "impossible reading" rule uses −4 bar.

---

## 2. Project structure

```
IotProject/                  # CLOUD laptop
├── config.json              # broker, topics, thresholds
├── requirements.txt
├── start_all.ps1            # starts Node-RED + decision engine
├── wds_iot_device.zip       # give this to the DEVICE laptop (not in git; rebuild with tools/)
├── WDSEventDB-main/         # dataset (for training)
├── iot_device/              # source of the device package
├── tools/build_device_zip.py# builds wds_iot_device.zip from iot_device/ + config.json
├── broker/                  # local MQTT broker, only as a backup without internet
├── common/                  # shared Python code
│   ├── config.py            #   sensor names, classes, zones, scenarios
│   ├── dataset.py           #   Excel loading (+ CSV cache in data/cache)
│   ├── features.py          #   feature engineering + leak index / severity / sensor rules
│   ├── predictor.py         #   one inference API for every model
│   └── db.py                #   SQLite helpers (init / reset)
├── db/schema.sql            # SQLite schema
├── ml/
│   ├── train.py             # trains + evaluates the 3 models
│   ├── deep_models.py       # PyTorch CNN-LSTM
│   ├── models/              # saved models + meta.json (deployed model, thresholds)
│   └── results/             # metrics, report, plots, model_selection.md, closed_loop_kpis.json
├── engine/
│   ├── decision_engine.py   # analytics + decision + MQTT commands
│   └── kpi.py               # closed-loop performance metrics
├── node-red/                # Node-RED user dir: settings.js, flows.json, dashboard
└── data/wds.db              # SQLite database (created automatically)
```

---

## 3. Setup (one time)

**Cloud laptop.** Requirements: Python 3.10+, Node.js 22.5+ (Node 24 recommended; it has the built-in `node:sqlite`), Node-RED (`npm i -g node-red`).

```bash
pip install -r requirements.txt
```
```bash
cd node-red && npm install
```

> On this network `registry.npmjs.org` resets connections, so the folders contain an `.npmrc` that points npm at the identical mirror `https://registry.yarnpkg.com/`. Delete the `.npmrc` files if npmjs works for you.
>
> Node-RED talks to SQLite through Node's **built-in `node:sqlite`** module, opened in `node-red/settings.js` and shared with function nodes as `global.get('db')`. This avoids `node-red-node-sqlite`, whose native `sqlite3` add-on needs Visual Studio to compile on Windows.

**Device laptop.** Only Python 3.8+ and internet. Unzip `wds_iot_device.zip`; the MQTT library and the dataset are inside.

Train the models (about 30 seconds; the trained models are already included):
```bash
python -m ml.train
```

---

## 4. Running

**Cloud laptop:**
```bash
powershell -ExecutionPolicy Bypass -File start_all.ps1
```

**Device laptop:** double-click `run_device.bat` in the unzipped folder (Mac/Linux: `sh run_device.sh`).

Either laptop can start first. Both connect to `broker.hivemq.com:1883`.

* Dashboard (cloud laptop): http://localhost:1880/ui
* Node-RED editor: http://localhost:1880
* Useful options: `python -m engine.decision_engine --model cnn_lstm --no-auto`, `python iot_device.py --scenario leak1 --rate 10`, `python -m common.db --reset`
* After changing the broker or topics in `config.json`, rebuild the zip with `python tools/build_device_zip.py` and give it to the device laptop again.

The device plays the playlist in its `device_config.json` (clean → leak1 → clean → sensor fault → leak2 → cyber-attack → leak3 → sensor fault) and loops. Each recording row is one reading.

### The public broker
`broker.hivemq.com` is free and needs no account, but **anyone on the internet can use it**. All topics start with the random project id `wds-t2-906e031a` so that our traffic doesn't mix with other people's, and client ids are unique because a public broker disconnects duplicates. This is fine for a demo with a public dataset, but it isn't real security: someone who found the topic names could read the data or send commands. A real deployment would use a private broker with TLS and passwords (see Limitations).

### Backup without internet
If HiveMQ or the internet is down, on the cloud laptop set `"host": "localhost"` in `config.json` and run `start_all.ps1 -LocalBroker`. Start the device with its host set to the cloud laptop's IP address on the same Wi-Fi: `python iot_device.py --host <cloud-laptop-IP>`. Windows Firewall on the cloud laptop must allow port 1883. A phone hotspot also helps if a college network blocks MQTT. This backup route has not been tested across two laptops.

### Dashboard pages

| Page | Content |
|---|---|
| **Live Monitoring** | pressure and flow charts, pressure gauges, pump/valve positions, data source and quality |
| **Analytics & Alerts** | confirmed system state (severity, zone, faulty sensor), class probabilities, prediction vs ground-truth timeline, leak index, alert table + pop-ups, actuator state (valves, pumps, mode), control action log with ack times |
| **Control Panel** | auto mode on/off, **switch ML model live**, confidence and vote thresholds, manual override (isolate zone A-D, safe mode, restore, pump setpoint), **scenario injection** (leak / sensor fault / cyber-attack on demand, played by the device laptop), replay speed, device online/offline status, clear database |
| **Performance** | live closed-loop KPIs, model comparison table + plots, database row counts |

---

## 5. Analytics & ML

### Features (`common/features.py`)
53 features per reading, all computed only from past readings, so the online engine gets exactly what the model saw in training:
raw 15 signals; for each pressure and flow a 10-reading rolling mean and std, the deviation from the 60-reading mean, and the first difference; hydraulic indicators (head P1−P2, P4−P3, minimum pressure, total outflow, pump-1 speed per unit flow, valve difference).
The CNN-LSTM instead takes the raw sequence of the last 30 readings.

### Train/test split
Each recording is cut into contiguous blocks of 60 readings, and 30 % of the blocks of every class go to the test set. Neighbouring, almost identical readings therefore never end up on both sides of the split, which a random row split would allow.
Train 17,622 readings, test 7,543.

### Results (`ml/results/report.md`)

One model from each family: the best by macro F1 out of 9 candidates (`ml/results/model_selection.md`).

| Model | Family | Accuracy | Macro F1 | False-alarm rate | Leak recall | Train [s] | Size [KB] | Latency [ms]* |
|---|---|---|---|---|---|---|---|---|
| k-NN | Traditional | 0.989 | 0.970 | 0.34 % | 0.923 | 0.04 | 3,789 | 6.9 |
| **Extra Trees (deployed)** | Ensemble | **0.999** | **0.996** | **0.06 %** | **0.991** | 0.25 | 5,679 | 13.8 |
| CNN-LSTM | Deep learning | 0.985 | 0.944 | 1.57 % | 0.987 | 11.76 | 109 | 0.8 |

\* time to classify one new reading **including feature extraction** (the CNN-LSTM needs no hand-made features, so it is the fastest).
Deep-learning training varies slightly between runs: CNN-LSTM scored F1 0.950 in the selection run and 0.944 in this one, so its lead over the MLP (0.945) is within normal variation.
Also reported: precision, recall, per-class metrics, CPU %, memory, confusion matrices (`confusion_matrices.png`), comparison chart (`model_comparison.png`).

### Leak severity and location (physics-based, thresholds learned from data)
For every zone (A = P1/F1, B = P2/F2, C = P3/F3, D = P4/F4):
`zone score = relative flow increase + relative pressure drop` compared with a baseline of recent normal readings.
`leak index = max(zone scores)`. The zone with the highest score is the **suspected leak zone**.
Severity thresholds are the 33rd/66th percentiles of the leak index over all leak readings: **minor < 0.257 ≤ moderate < 1.201 ≤ severe**.

---

## 6. Decision & automation (`engine/decision_engine.py`)

1. **Hybrid detection**: ML prediction plus a physical rule (a pressure outside −4…15 bar is always a sensor fault).
2. **Stability (debouncing)**: an event is confirmed only when 3 of the last 5 predictions agree with confidence ≥ 0.6. It is cleared only after 15 consecutive normal readings, which prevents rapid valve switching.
3. **Actions**

| Event | Alert | Control action |
|---|---|---|
| Leak, minor | WARNING | pump 1 → 85 % (pressure management reduces leakage) |
| Leak, moderate | CRITICAL | pump 1 → 70 %, **close isolation valve of the suspected zone** |
| Leak, severe | CRITICAL | pump 1 → 50 %, isolate zone (escalation only, never de-escalates mid-event) |
| Sensor fault | WARNING (+ which sensor) | flag sensor for maintenance, **no hydraulic action** (avoids unnecessary switching) |
| Cyber-attack | CRITICAL | controller to **SAFE mode** (locks remote setpoints) |
| Back to normal | INFO | `restore`: open valves, pumps 100 %, clear flags |
| Pump inefficiency | WARNING | advisory when pump-1 speed per unit flow is 1.6× the normal value |

4. **Manual mode**: with auto mode OFF the engine only sends *recommended actions* as alerts, and the operator acts from the Control Panel.
5. Everything is configurable in `config.json → decision` or live from the dashboard (`wds-t2-906e031a/engine/config`).

**Closed loop on recorded data**: a recording cannot react to a valve, so the IoT node approximates the effect. When the leaking section is isolated (or safe mode blocks an attack), the replay skips the rest of that anomalous run after the 2 s valve travel time, i.e. the network returns to normal. The engine then sees normal data and sends `restore`. Pump-speed changes and sensor flags are recorded but do not change the replayed values.

---

## 7. Performance evaluation

### Closed-loop KPIs

| KPI | Value |
|---|---|
| True events / detected / missed / false | 12 / 12 / 0 / 1 (a brief false sensor-fault event, from Extra Trees) |
| Online accuracy | 99.72 % |
| Broker round trip (cloud → HiveMQ → device → cloud) | ~412 ms at best |
| Detection delay | 5 readings on average (3-11), ~940 ms |
| Response time (command → ack back at the cloud) | ~1,060 ms (includes the 300 ms simulated actuator delay) |
| End-to-end latency (sensor reading → ack back at the cloud) | ~1,560 ms |
| Leaks contained / attacks blocked | 3 / 1 |

Measured over the real HiveMQ broker at 10 readings/s, with the device and cloud running on one laptop and the device clock deliberately shifted by 7 s (the engine measured 6,985 ms and corrected for it). Numbers depend on the internet connection, so two laptops will differ. A two-laptop run has not been measured yet.

Live values are on the dashboard *Performance* page and are saved to `ml/results/closed_loop_kpis.json` and to the `kpi_log` table.
"Unnecessary actions" counts corrective commands sent while the ground truth was normal (stability). The "alarm-state share while normal" includes the intentional 15-reading hold-off before restore.

### Two laptops, two clocks
Sensor timestamps come from the device's clock, and the laptops' clocks never match exactly. Every 5 s the engine sends a ping and the device replies with its time, which gives an NTP-style estimate of the clock offset. Sensor times are converted to the cloud clock with it. Response times are measured only on the cloud clock (command sent → ack received). Both the offset and the broker round trip are shown on the *Performance* page.

---

## 8. MQTT topics

| Topic | Direction | Payload |
|---|---|---|
| `wds-t2-906e031a/sensors/telemetry` | IoT node → | reading: 15 signals, `ts`, `seq`, `scenario`, `segment`, `gt_class` (ground truth, evaluation only) |
| `wds-t2-906e031a/analytics/prediction` | engine → | class, confidence, probabilities, state, severity, zone, latency |
| `wds-t2-906e031a/alerts` | engine / Node-RED → | level, type, message, zone/sensor |
| `wds-t2-906e031a/control/cmd` | engine / dashboard → IoT node | `{cmd_id, action, target, value, reason}`; actions: `set_pump`, `isolate_zone`, `open_valve`, `close_valve`, `safe_mode`, `flag_sensor`, `restore` |
| `wds-t2-906e031a/actuators/state` | IoT node → | acknowledgement with `cmd_ts`, `ack_ts`, effect, full actuator state |
| `wds-t2-906e031a/actuators/state/current` | IoT node → (retained) | current actuator state |
| `wds-t2-906e031a/engine/config` / `…/current` | dashboard ↔ engine | model, auto mode, thresholds, KPI reset |
| `wds-t2-906e031a/engine/kpi` | engine → | closed-loop KPIs every 2 s |
| `wds-t2-906e031a/sim/control` / `wds-t2-906e031a/sim/status` | dashboard ↔ IoT node | scenario injection, rate, pause / replay status, online/offline (Last Will) |
| `wds-t2-906e031a/engine/ping` / `wds-t2-906e031a/device/pong` | engine ↔ IoT node | clock offset and round-trip measurement |

## 9. Database tables (`db/schema.sql`)
`telemetry` (every reading), `predictions` (one per reading), `alerts`, `actions` (command + ack time + response ms + effect), `kpi_log`.

---

## 10. Viva quick reference: where to change things

| Change | Where |
|---|---|
| Replay speed / scenario order | Dashboard speed slider, or `device_config.json` on the device laptop |
| Detection sensitivity | `decision.votes_required`, `min_confidence` (live sliders) |
| Pump reduction per severity, isolation threshold | `decision.pump_reduction`, `isolate_from_severity` |
| Physical valid ranges | `decision.pressure_valid_range`, `flow_valid_range` (engine + Node-RED edge rule) |
| Add a new action | `execute_command()` in `iot_device.py` (device) + `start_event()/update_leak()` in the engine (cloud) |
| Add a model | `sklearn_models()` in `ml/train.py`, retrain, then pick it in the dashboard |
| Edge rule in Node-RED | function node *Validate & enrich* (flow tab *Ingest*) |
| SQL inserts | function nodes *Store …* (they use `global.get('db')`) |

Restart Node-RED after changing `config.json`, because it reads the file at start-up.

### Limitations
* Recorded data cannot respond to control actions. Their effect is approximated by skipping the rest of the anomalous run (see §6).
* Leak location is a heuristic (the dataset has no true leak position).
* Water saved is reported in dataset flow units × seconds.
* The public HiveMQ broker has no access control or encryption. A private HiveMQ Cloud cluster with TLS and passwords would fix this, but needs an account and changes to the broker settings on both sides.
