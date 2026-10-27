# Smart Water Distribution and Leakage Detection (Team 2)

IoT mini-project, B.Tech CSE Sem 7. It uses the real **WDSEventDB** water-distribution testbed dataset and follows the given architecture end to end:

```
IoT node (dataset replay) ─①─► MQTT broker ─②─► Node-RED ─③─► SQLite + Dashboard
                                   ▲                              │④
                                   │⑧ actuator commands           ▼
                              IoT node ◄─⑦─ MQTT ◄─ Decision engine ◄─⑤─ Analytics & ML
```

| Arrow | What happens | Where |
|---|---|---|
| ① | Sensor readings (4 pressure, 4 flow, 5 pump VFD, 2 valve) published as JSON on `wds/sensors/telemetry` | `simulator/iot_node.py` |
| ② | Node-RED subscribes, validates (range check, edge alert) and enriches each reading | flow tab *1-3 Ingest* |
| ③ | Readings stored in SQLite (`telemetry` table) and sent to the Node-RED Dashboard | flow tabs *Ingest* and *Dashboard* |
| ④ | The decision engine reads every new row from SQLite | `engine/decision_engine.py` |
| ⑤ | ML model classifies NORMAL / LEAK / SENSOR_FAULT / CYBER_ATTACK; the decision logic confirms the event and works out severity and zone | `engine/`, `common/predictor.py` |
| ⑦ | Control commands published on `wds/control/cmd` | decision engine, dashboard *Control Panel* |
| ⑧ | IoT node actuators (pumps, isolation valves, safe mode) execute the command and ack on `wds/actuators/state` | `simulator/iot_node.py` |

---

## 1. Dataset (WDSEventDB)

The dataset has 15 signals per reading: `Pressure 1 Out`, `Pressure 2 Out`, `Pressure 3 In`, `Pressure 4 In`, `Water Flow 1-4`, `VFD 1, 2, 3, 4-1, 4-2` (pump drives) and `Analog Valve 1-2`. The `Labels` column is 0 = normal, 1 = event.

| Folder | Files | Event |
|---|---|---|
| Clean | CleanData.xlsx (9,743 readings) | none |
| Leakage | LeakEvent1-3 | leak (label 1 from the leak onset) |
| Sensor Failure | SensorEvent1, 23, 45, 67 | sensor failure on P1 / P2 / P3+P4 / P4 |
| Cyberattack | CyberEvent1-4 | 4 attack windows (valve 2 forced to ~100 %, pump 1 reduced) |

The binary label is combined with the folder name to make a **4-class** target:
`0 NORMAL, 1 LEAK, 2 SENSOR_FAULT, 3 CYBER_ATTACK`.

> The dataset has no tank-level sensor, so tank level is not used. Pump status comes from the VFD signals.
> Note: the inlet pressures P3/P4 legitimately go down to about −2.8 bar in normal operation (suction side), so the "impossible reading" rule uses −4 bar.

---

## 2. Project structure

```
IotProject/
├── config.json              # all settings: topics, rates, thresholds, playlist
├── requirements.txt
├── start_all.ps1            # starts broker, Node-RED, engine, simulator
├── WDSEventDB-main/         # dataset (unzipped)
├── broker/                  # MQTT broker (Aedes, Node.js) + mosquitto.conf alternative
├── common/                  # shared Python code
│   ├── config.py            #   sensor names, classes, zones, scenarios
│   ├── dataset.py           #   Excel loading (+ CSV cache in data/cache)
│   ├── features.py          #   feature engineering + leak index / severity / sensor rules
│   ├── predictor.py         #   one inference API for every model
│   └── db.py                #   SQLite helpers (init / reset)
├── db/schema.sql            # SQLite schema
├── simulator/iot_node.py    # IoT node: sensors (publish) + actuators (subscribe)
├── ml/
│   ├── train.py             # trains + evaluates 9 models
│   ├── deep_models.py       # PyTorch MLP and CNN-LSTM
│   ├── models/              # saved models + meta.json (deployed model, thresholds)
│   └── results/             # metrics.json, report.md, plots, closed_loop_kpis.json
├── engine/
│   ├── decision_engine.py   # analytics + decision + MQTT commands
│   └── kpi.py               # closed-loop performance metrics
├── node-red/                # Node-RED user dir: settings.js, flows.json, dashboard
└── data/wds.db              # SQLite database (created automatically)
```

---

## 3. Setup (one time)

Requirements: Python 3.10+, Node.js 22.5+ (Node 24 recommended; it has the built-in `node:sqlite`), Node-RED (`npm i -g node-red`).

```bash
pip install -r requirements.txt
```
```bash
cd broker && npm install
```
```bash
cd node-red && npm install
```

> On this network `registry.npmjs.org` resets connections, so the folders contain an `.npmrc` that points npm at the identical mirror `https://registry.yarnpkg.com/`. Delete the `.npmrc` files if npmjs works for you.
>
> Node-RED talks to SQLite through Node's **built-in `node:sqlite`** module, opened in `node-red/settings.js` and shared with function nodes as `global.get('db')`. This avoids `node-red-node-sqlite`, whose native `sqlite3` add-on needs Visual Studio to compile on Windows.

Train the models (about 1 minute; the trained models are already included):
```bash
python -m ml.train
```

---

## 4. Running

**All at once (Windows):**
```bash
powershell -ExecutionPolicy Bypass -File start_all.ps1 -Rate 5
```

**Or one terminal each, in this order:**
```bash
node broker/broker.js
```
```bash
cd node-red && node-red --userDir . --settings settings.js
```
```bash
python -m engine.decision_engine
```
```bash
python -m simulator.iot_node --rate 5
```

* Dashboard: http://localhost:1880/ui
* Node-RED editor: http://localhost:1880
* Useful options: `python -m engine.decision_engine --model cnn_lstm --no-auto`, `python -m simulator.iot_node --scenario leak1 --rate 10`, `python -m common.db --reset`

The simulator plays the playlist in `config.json` (clean → leak1 → clean → sensor fault → leak2 → cyber-attack → leak3 → sensor fault) and loops. Each recording row is one reading; `--rate` sets readings per second.

### Dashboard pages

| Page | Content |
|---|---|
| **Live Monitoring** | pressure and flow charts, pressure gauges, pump/valve positions, data source and quality |
| **Analytics & Alerts** | confirmed system state (severity, zone, faulty sensor), class probabilities, prediction vs ground-truth timeline, leak index, alert table + pop-ups, actuator state (valves, pumps, mode), control action log with ack times |
| **Control Panel** | auto mode on/off, **switch ML model live**, confidence and vote thresholds, manual override (isolate zone A-D, safe mode, restore, pump setpoint), **scenario injection** (leak / sensor fault / cyber-attack on demand), replay speed, clear database |
| **Performance** | live closed-loop KPIs, offline model comparison table + plots, database row counts |

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

| Model | Family | Accuracy | Macro F1 | False-alarm rate | Leak recall | Train [s] | Size [KB] | Latency [ms]* |
|---|---|---|---|---|---|---|---|---|
| Logistic regression | Traditional | 0.967 | 0.899 | 3.11 % | 0.937 | 0.26 | 3 | 7.0 |
| k-NN | Traditional | 0.989 | 0.970 | 0.34 % | 0.923 | 0.02 | 3,789 | 7.6 |
| Decision tree | Traditional | 0.964 | 0.899 | 2.83 % | 0.866 | 0.29 | 10 | 5.9 |
| Naive Bayes | Traditional | 0.347 | 0.499 | 75.1 % | 0.858 | 0.02 | 4 | 6.3 |
| Random forest | Ensemble | 0.997 | 0.987 | 0.08 % | 0.987 | 0.59 | 2,484 | 14.2 |
| **Extra trees (deployed)** | Ensemble | **0.999** | **0.996** | **0.06 %** | **0.991** | 0.24 | 5,679 | 14.3 |
| Gradient boosting | Ensemble | 0.996 | 0.985 | 0.22 % | 0.989 | 4.27 | 1,094 | 10.6 |
| MLP | Deep learning | 0.984 | 0.945 | 1.37 % | 0.945 | 5.55 | 64 | 6.1 |
| CNN-LSTM | Deep learning | 0.987 | 0.950 | 1.38 % | 0.992 | 10.63 | 109 | 0.8 |

\* time to classify one new reading **including feature extraction** (the CNN-LSTM needs no hand-made features, so it is the fastest).
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
5. Everything is configurable in `config.json → decision` or live from the dashboard (`wds/engine/config`).

**Closed loop on recorded data**: a recording cannot react to a valve, so the IoT node approximates the effect. When the leaking section is isolated (or safe mode blocks an attack), the replay skips the rest of that anomalous run after the 2 s valve travel time, i.e. the network returns to normal. The engine then sees normal data and sends `restore`. Pump-speed changes and sensor flags are recorded but do not change the replayed values.

---

## 7. Performance evaluation

### Closed-loop KPIs (one full playlist at 20 readings/s, deployed Extra-Trees model)

| KPI | Value |
|---|---|
| Online accuracy | 99.85 % (leak recall 97.9 %, sensor fault 100 %, cyber 100 %) |
| True events / detected / missed | 8 / 8 / 0 |
| Detection delay | 4 readings on average (3-11), ≈195 ms at 20 Hz |
| Response time (command → actuator ack) | 309 ms (300 ms is the simulated actuator delay) |
| End-to-end latency (sensor reading → actuator ack) | 359 ms |
| Pipeline latency sensor → prediction (MQTT → Node-RED → SQLite → ML) | 44 ms |
| Leaks contained / attacks blocked | 3 / 1 |
| Decision engine resources | ~34 % of one CPU core, ~160 MB RAM at 20 readings/s |

Live values are on the dashboard *Performance* page and are saved to `ml/results/closed_loop_kpis.json` and to the `kpi_log` table.
"Unnecessary actions" counts corrective commands sent while the ground truth was normal (stability). The "alarm-state share while normal" includes the intentional 15-reading hold-off before restore.

---

## 8. MQTT topics

| Topic | Direction | Payload |
|---|---|---|
| `wds/sensors/telemetry` | IoT node → | reading: 15 signals, `ts`, `seq`, `scenario`, `segment`, `gt_class` (ground truth, evaluation only) |
| `wds/analytics/prediction` | engine → | class, confidence, probabilities, state, severity, zone, latency |
| `wds/alerts` | engine / Node-RED → | level, type, message, zone/sensor |
| `wds/control/cmd` | engine / dashboard → IoT node | `{cmd_id, action, target, value, reason}`; actions: `set_pump`, `isolate_zone`, `open_valve`, `close_valve`, `safe_mode`, `flag_sensor`, `restore` |
| `wds/actuators/state` | IoT node → | acknowledgement with `cmd_ts`, `ack_ts`, effect, full actuator state |
| `wds/actuators/state/current` | IoT node → (retained) | current actuator state |
| `wds/engine/config` / `…/current` | dashboard ↔ engine | model, auto mode, thresholds, KPI reset |
| `wds/engine/kpi` | engine → | closed-loop KPIs every 2 s |
| `wds/sim/control` / `wds/sim/status` | dashboard ↔ IoT node | scenario injection, rate, pause / replay status |

## 9. Database tables (`db/schema.sql`)
`telemetry` (every reading), `predictions` (one per reading), `alerts`, `actions` (command + ack time + response ms + effect), `kpi_log`.

---

## 10. Viva quick reference: where to change things

| Change | Where |
|---|---|
| Replay speed / scenario order | `config.json → simulator`, or the dashboard |
| Detection sensitivity | `decision.votes_required`, `min_confidence` (live sliders) |
| Pump reduction per severity, isolation threshold | `decision.pump_reduction`, `isolate_from_severity` |
| Physical valid ranges | `decision.pressure_valid_range`, `flow_valid_range` (engine + Node-RED edge rule) |
| Add a new action | `execute_command()` in `simulator/iot_node.py` + `start_event()/update_leak()` in the engine |
| Add a model | `sklearn_models()` in `ml/train.py`, retrain, then pick it in the dashboard |
| Edge rule in Node-RED | function node *Validate & enrich* (flow tab *Ingest*) |
| SQL inserts | function nodes *Store …* (they use `global.get('db')`) |

Restart Node-RED after changing `config.json`, because it reads the file at start-up.

### Limitations
* Recorded data cannot respond to control actions. Their effect is approximated by skipping the rest of the anomalous run (see §6).
* Leak location is a heuristic (the dataset has no true leak position).
* Water saved is reported in dataset flow units × seconds.
