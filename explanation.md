# Project Explanation: Smart Water Distribution and Leakage Detection

This explains the whole project for the assessment and viva: what it does, which technologies it uses and why, how one sensor reading travels through the system step by step, how the ML and decision-making work, and how to demo and modify it live.

---

## 1. The problem in one paragraph

A water-distribution network (pipes, pumps, valves) must be watched all the time. Three things can go wrong:
- a **leak**, where water escapes so flow goes up and pressure goes down
- a **sensor failure**, where a sensor reports nonsense (e.g. −12 bar)
- a **cyber-attack**, where someone remotely manipulates valves or pumps

Our system **monitors** the network with IoT sensors and **detects** which of these is happening using machine learning. It then **acts automatically**: it slows the pump, closes the valve of the leaking section, flags the broken sensor, or switches the controller to a safe mode. It **restores** normal operation once the problem is gone.

---

## 2. Architecture (the diagram we were given)

```
 ┌────────────┐ ① ┌────────────┐ ② ┌──────────┐ ③ ┌───────────┐
 │ IoT Devices│──►│ MQTT Broker│──►│ Node-RED │──►│ Dashboard │
 │ (sensors / │   │            │   │  (flow   │   └───────────┘
 │ simulator) │   │            │   │processing)──►┌───────────┐ ④ ┌──────────────┐ ⑤ ┌──────────────┐
 └────────────┘   └────────────┘   └──────────┘   │ Database  │──►│ Analytics &  │──►│   Decision   │
       ▲                ▲                         │ (SQLite)  │   │      ML      │   │    Engine    │
       │ ⑧              │ ⑦                       └───────────┘   └──────────────┘   └──────┬───────┘
       └────────────────┴───────────── control command (via MQTT) ────────────────────────┘
```

| Step | Meaning | Our implementation |
|---|---|---|
| ① | Devices publish sensor data | `simulator/iot_node.py` publishes JSON to topic `wds/sensors/telemetry` |
| ② | Broker delivers it to Node-RED | MQTT broker (`broker/broker.js`); Node-RED subscribes with an *mqtt in* node |
| ③ | Node-RED processes and forwards it | validation + SQLite insert + dashboard widgets |
| ④ | ML reads from the database | `engine/decision_engine.py` reads new rows from `data/wds.db` |
| ⑤ | ML result goes to the decision engine | same Python program: classify → confirm → decide |
| ⑦ | Decision is published as a command | topic `wds/control/cmd` |
| ⑧ | Broker delivers the command to actuators | the IoT node subscribes, operates pumps/valves, replies with an acknowledgement |

---

## 3. Technologies used and why

| Technology | Used for | Why this choice |
|---|---|---|
| **Python 3.12** | simulator, ML training, decision engine | best ecosystem for data and ML |
| **pandas / numpy** | reading the Excel dataset, feature engineering | standard tabular-data tools |
| **openpyxl** | lets pandas read `.xlsx` files | the dataset is in Excel format |
| **scikit-learn** | traditional and ensemble ML models | simple, reliable, well documented |
| **PyTorch** | deep-learning models (MLP, CNN-LSTM) | flexible deep-learning library |
| **joblib** | saving scikit-learn models to disk | standard way to persist sklearn models |
| **paho-mqtt** | MQTT client in Python | official Eclipse MQTT client |
| **psutil** | measuring CPU and memory use | needed for "computational performance" |
| **matplotlib** | confusion-matrix and comparison plots | standard plotting library |
| **MQTT** | all device ↔ system communication | lightweight publish/subscribe protocol designed for IoT |
| **Aedes** (Node.js) | the MQTT broker | pure JavaScript, no install needed; Mosquitto also works (`broker/mosquitto.conf`) |
| **Node-RED 5** | integration / flow processing | visual flow-based tool required by the brief |
| **node-red-dashboard** | web dashboard (charts, gauges, buttons) | the "Node-RED Dashboard" required by the brief |
| **SQLite** | database | file-based, zero setup; allowed by the brief (SQLite/MySQL) |
| **node:sqlite** | SQLite access from Node-RED | built into Node.js 24; the usual `node-red-node-sqlite` needs a C++ compiler on Windows |

### Key MQTT concepts we use (likely viva questions)
- **Publish/subscribe**: senders (publishers) and receivers (subscribers) never talk directly. The **broker** routes each message by its **topic**, e.g. `wds/sensors/telemetry`.
- **QoS** (Quality of Service): QoS 0 = at most once (fire and forget), QoS 1 = at least once (acknowledged), QoS 2 = exactly once. We use **QoS 0 for telemetry**, because many readings arrive and losing one doesn't matter. We use **QoS 1 for commands, alerts and acks**, because they must arrive.
- **Retained message**: the broker keeps the last message on a topic and gives it to anyone who subscribes later. We retain the actuator state, the simulator status and the engine settings, so a freshly opened dashboard shows the current state at once.
- **Last Will and Testament (LWT)**: a message the broker publishes if a client disconnects unexpectedly. The IoT node's will is `{"online": false}` on `wds/sim/status`, so the dashboard shows OFFLINE if it crashes.

---

## 4. The dataset (WDSEventDB)

Real data recorded from a laboratory water-distribution testbed. Each row is one reading (about one per second) with 15 signals:

| Group | Columns | Meaning |
|---|---|---|
| Pressure | `Pressure 1 Out`, `Pressure 2 Out`, `Pressure 3 In`, `Pressure 4 In` | bar; "In" sensors are on the inlet/suction side and can be slightly negative normally |
| Flow | `Water Flow 1-4` | flow at 4 points |
| Pumps | `VFD 1`, `VFD 2`, `VFD 3`, `VFD 4-1`, `VFD 4-2` | pump speed via Variable Frequency Drive |
| Valves | `Analog Valve 1`, `Analog Valve 2` | valve opening % |
| Label | `Labels` | 0 = normal, 1 = event |

| Folder | File(s) | Readings | Event inside |
|---|---|---|---|
| Clean | CleanData.xlsx | 9,743 | none |
| Leakage | LeakEvent1, 2, 3 | ~1,370 each | normal first, then a leak |
| Sensor Failure | SensorEvent1, 23, 45, 67 | ~1,360 each | one or more sensors fail (values like −8 to −13) |
| Cyberattack | CyberEvent1-4 | 5,869 | 4 attack windows: valve 2 forced to ~100 %, pump 1 slowed |

The label is only 0/1, so we combine it with the folder name to create a **4-class target**:
`0 = NORMAL, 1 = LEAK, 2 = SENSOR_FAULT, 3 = CYBER_ATTACK` (`common/config.py`, `common/dataset.py`).

The network has no tank-level sensor, so we monitor pressure, flow, pumps and valves (pump status comes from the VFD speeds).

We divide the network into **4 zones**, each watched by one pressure and one flow sensor:
Zone A = P1/F1 (main supply after pump 1), Zone B = P2/F2, Zone C = P3/F3, Zone D = P4/F4. Each zone has an isolation valve `iso_A … iso_D`.

---

## 5. Folder map

```
IotProject/
├── config.json            ← every setting (topics, speeds, thresholds, playlist)
├── start_all.ps1          ← starts all 4 programs
├── README.md              ← setup / run instructions
├── explanation.md         ← this file
├── WDSEventDB-main/       ← dataset
├── broker/broker.js       ← MQTT broker
├── simulator/iot_node.py  ← IoT device: sensors + actuators
├── node-red/              ← Node-RED: settings.js (DB connection), flows.json (all flows + dashboard)
├── db/schema.sql          ← database tables
├── common/                ← shared Python code (config, dataset, features, predictor, db)
├── ml/train.py            ← trains and compares 9 models
├── ml/deep_models.py      ← PyTorch networks
├── ml/models/             ← trained models + meta.json
├── ml/results/            ← metrics, report, plots
└── engine/                ← decision_engine.py (brain) + kpi.py (performance measurement)
```

---

## 6. Start-up order (and why)

1. **MQTT broker** first: everyone else connects to it.
2. **Node-RED**: opens the SQLite database, creates the tables if missing, subscribes to the topics, serves the dashboard.
3. **Decision engine**: loads the ML model, connects to MQTT, starts watching the database for new rows.
4. **IoT node simulator** last: starts publishing readings.

`start_all.ps1` does exactly this, with short pauses in between.

---

## 7. The life of ONE sensor reading (the complete sequence)

This is the most important section for the viva. Follow one reading from start to finish.

### Step ①: the IoT node publishes the reading
`simulator/iot_node.py → publish_reading()`
- It takes the next row of the current recording, e.g. row 600 of `LeakEvent1`.
- It builds a JSON message:
  ```json
  {"device_id": "wds-node-01", "seq": 731, "ts": 1793082903981,
   "scenario": "leak1", "segment": 2, "row": 600,
   "p1_out": 5.66, "p2_out": 0.83, "p3_in": 0.64, "p4_in": 1.09,
   "f1": 0.59, "f2": 0.59, "f3": 0.60, "f4": 0.39,
   "vfd1": 50.6, "vfd2": 0, "vfd3": 41, "vfd4_1": 0, "vfd4_2": 43,
   "valve1": 72.4, "valve2": 71.5,
   "gt_class": 1, "gt_name": "LEAK"}
  ```
- `ts` is the time the reading was "measured" (ms since 1970). `gt_class` is the **ground truth** from the dataset label. It is used **only for scoring** our system, never as model input.
- It publishes to `wds/sensors/telemetry` at the configured rate (default 5 readings/second).

### Step ②: the broker delivers it to Node-RED
The broker sees that Node-RED subscribed to `wds/sensors/telemetry` and forwards the message. The *mqtt in* node parses the JSON into a JavaScript object (`msg.payload`).

### Step ③: Node-RED processes, stores and displays it
Flow tab **"1-3 Ingest"**:
1. **Validate & enrich** (function node):
   - checks every pressure is within −4…15 bar and every flow within −0.05…50
   - adds `received_ts` and `transit_ms` (how long MQTT took)
   - adds `quality = OK / SUSPECT`
   - **edge rule**: if a value is impossible, it immediately publishes an alert to `wds/alerts` (at most once every 30 s). This is processing *at the edge*, before any ML.
2. **Store telemetry (SQLite)**: runs `INSERT INTO telemetry (...) VALUES (...)` using a prepared statement and records the new row `id`.
3. A **link node** sends the message to the **Dashboard** flow, which splits it into the pressure chart, flow chart, 4 gauges, the pump/valve bars and the data-source table.

### Step ④: the decision engine reads it from the database
`engine/decision_engine.py → run()`
- In a loop, it runs `SELECT * FROM telemetry WHERE id > last_id ORDER BY id` (polling about every 50 ms).
- It appends the new row to a **window** of the last 60 readings, because the features need history.
- If the `segment` number changed (the simulator jumped to another recording), it clears the window, because recordings are not continuous with each other.

### Step ⑤: analytics and ML
`classify()`
1. **Feature engineering**: turns the last 60 readings into 53 numbers (Section 8).
2. **ML model** (`common/predictor.py`): gives 4 probabilities, e.g. `NORMAL 0.02, LEAK 0.97, SENSOR_FAULT 0.01, CYBER 0.00`. The prediction is the highest one (LEAK, confidence 0.97).
3. **Physical rule** (hybrid AI): if a pressure is physically impossible, the result is forced to SENSOR_FAULT, whatever the model says.

### Step ⑤ continued: the decision engine
1. **Voting / debouncing**: the prediction goes into a list of the last 5 predictions. An event is **confirmed** only when **3 of the last 5** agree with confidence ≥ 0.6. One noisy reading can never trigger an action.
2. When a LEAK is confirmed:
   - **Severity**: it compares recent readings with a **baseline** (median of recent normal readings) and computes the **leak index** → minor / moderate / severe.
   - **Location**: the zone with the highest leak score is the suspected zone.
3. It **raises an alert** (`wds/alerts`), for example `CRITICAL LEAK: Leak detected: MODERATE (leak index 1.11), suspected zone C`.
4. It **sends commands** (step ⑦).
5. It also publishes the prediction for every reading to `wds/analytics/prediction`. Node-RED stores it in the `predictions` table and shows it on the *Analytics* page.

### Step ⑦: the command is published
```json
{"cmd_id": "eng-1793082925-2", "ts": 1793082925120, "source": "engine",
 "action": "isolate_zone", "target": "C", "reason": "moderate leak in zone C"}
```
Node-RED also listens on `wds/control/cmd` and saves each command to the `actions` table.

### Step ⑧: the actuator executes and acknowledges
`simulator/iot_node.py → execute_command()`
- It waits 300 ms (a realistic actuator delay), then changes its state: valve `iso_C = CLOSED`.
- It publishes an **acknowledgement** on `wds/actuators/state` containing `cmd_ts` and `ack_ts`, from which the response time is computed (≈ 300 ms).
- Node-RED updates the `actions` row (`ack_ts`, `response_ms`) and the *Actuator State* panel turns Zone C red.

### Closing the loop
- The data is recorded, so it cannot physically react to the closed valve. The simulator therefore **models the effect**: 2 s later (valve travel time) it skips the rest of the leak in the recording. The network becomes normal, as it would after a real isolation.
- The engine now sees NORMAL readings. After **15 normal readings in a row** it raises `INFO RESOLVED` and sends `restore` (open all valves, pumps to 100 %, clear sensor flags).
- The KPI tracker records how fast everything happened (Section 11).

### Timing measured in testing
| Stage | Time |
|---|---|
| Sensor → Node-RED (MQTT) | ~1 ms |
| Sensor → prediction (MQTT → Node-RED → SQLite → ML) | ~44 ms |
| ML inference (incl. features) | ~14 ms |
| Detection after true leak onset | ~4 readings (the vote needs 3 agreeing predictions) |
| Command → actuator acknowledgement | ~309 ms (300 ms simulated actuator delay) |
| Sensor reading → actuator acknowledgement (end to end) | ~360 ms |

---

## 8. Feature engineering (`common/features.py`)

A single reading isn't enough. A leak is better seen as a *change* compared with the recent past. For each reading we compute **53 features**:

| Feature type | How many | Example / purpose |
|---|---|---|
| Raw values | 15 | `p2_out = 0.83` |
| Rolling mean (10 readings) | 8 | smooths noise |
| Rolling standard deviation (10) | 8 | detects unstable or jumping values |
| Deviation from the 60-reading mean | 8 | "pressure dropped 0.9 compared with a minute ago" → sudden events |
| First difference | 8 | change since the previous reading |
| Hydraulic indicators | 6 | head loss P1−P2 and P4−P3, minimum pressure, total outflow F2+F3+F4, pump-1 speed per unit flow (efficiency), valve difference |

All features look **only backwards in time**, so the online engine computes exactly what the model saw during training.

---

## 9. Machine learning (`ml/train.py`)

### 9.1 Train/test split
Each recording is cut into **blocks of 60 consecutive readings**, and 30 % of the blocks of every class go to the test set.
*Why blocks and not random rows?* Neighbouring readings are almost identical. With a random split, a test row would have a near-copy in the training set and accuracy would be falsely high. Blocks avoid this leakage.
Result: 17,622 training readings and 7,543 test readings.

**Class imbalance**: there are many more normal readings than faults (15,264 normal vs 408 sensor-fault training rows). We use **class weights** (`class_weight="balanced"`, or a weighted loss in PyTorch), so mistakes on rare classes cost more.

### 9.2 The 9 models (three families, as in the diagram)

| Family | Model | How it works (one line) |
|---|---|---|
| Traditional | Logistic Regression | linear boundaries between classes |
| Traditional | k-Nearest Neighbours (k=7) | class of the most similar training readings |
| Traditional | Decision Tree | series of if/else rules on features |
| Traditional | Gaussian Naive Bayes | probability model assuming features are independent |
| Ensemble | Random Forest (150 trees) | many trees on random subsets, majority vote (**bagging**) |
| Ensemble | Extra Trees (150 trees) | like a random forest but with random split points, so less overfitting and faster |
| Ensemble | Gradient Boosting | trees built one after another, each fixing the previous errors (**boosting**) |
| Deep learning | MLP | neural network 53 → 128 → 64 → 4 on the features |
| Deep learning | CNN-LSTM | 1-D convolution + LSTM on the **raw last 30 readings**; learns temporal patterns itself, no hand-made features |

### 9.3 Results (test set)

| Model | Accuracy | Macro F1 | False alarms | Leak recall | Train time | Latency |
|---|---|---|---|---|---|---|
| Logistic Regression | 0.967 | 0.899 | 3.11 % | 0.937 | 0.26 s | 7.0 ms |
| k-NN | 0.989 | 0.970 | 0.34 % | 0.923 | 0.02 s | 7.6 ms |
| Decision Tree | 0.964 | 0.899 | 2.83 % | 0.866 | 0.29 s | 5.9 ms |
| Naive Bayes | 0.347 | 0.499 | 75.1 % | 0.858 | 0.02 s | 6.3 ms |
| Random Forest | 0.997 | 0.987 | 0.08 % | 0.987 | 0.59 s | 14.2 ms |
| **Extra Trees (deployed)** | **0.999** | **0.996** | **0.06 %** | **0.991** | 0.24 s | 14.3 ms |
| Gradient Boosting | 0.996 | 0.985 | 0.22 % | 0.989 | 4.27 s | 10.6 ms |
| MLP | 0.984 | 0.945 | 1.37 % | 0.945 | 5.55 s | 6.1 ms |
| CNN-LSTM | 0.987 | 0.950 | 1.38 % | 0.992 | 10.63 s | 0.8 ms |

**Model selection**: the model with the best macro F1 is deployed automatically (ties broken by lower latency). That's **Extra Trees**.

**Why ensembles win**: combining many trees reduces variance (overfitting) and copes well with tabular sensor features. **Why Naive Bayes fails**: it assumes features are independent, but our features are strongly correlated (the rolling mean of P1 is closely tied to P1 itself). **Why CNN-LSTM is fastest**: it skips the pandas feature computation, which is most of the ~14 ms.

### 9.4 Metric definitions (be ready to explain)
- **Accuracy** = correct / total. Misleading with imbalanced classes (predicting "normal" always would already score ~87 %).
- **Precision** (per class) = of the readings predicted as LEAK, how many really were leaks. Low precision means false alarms.
- **Recall** (per class) = of the real leaks, how many we caught. Low recall means missed leaks, which is dangerous.
- **F1** = harmonic mean of precision and recall. **Macro F1** averages it over the 4 classes equally, so rare classes count as much as normal. That's why we use it to choose the model.
- **False-alarm rate** = share of truly normal readings predicted as some fault.
- **Confusion matrix** = table of actual class vs predicted class (`ml/results/confusion_matrices.png`).
- RMSE / MAE / R² from the diagram are for **regression**. Our task is **classification**, so they don't apply ("as applicable").

### 9.5 Computational performance (also in the report)
Training time, CPU usage, memory growth during training, model file size, and **inference latency per reading** (measured exactly as the engine uses the model, including feature computation).

---

## 10. Decision engine in detail (`engine/decision_engine.py`)

### 10.1 Hybrid detection
`final class = ML prediction`, **except** when a pressure is outside −4…15 bar → forced to SENSOR_FAULT (the "rule" source). Combining data-driven ML with physical knowledge makes the system more robust.

### 10.2 Stability (prevents "unnecessary switching")
- **Confirm**: ≥ 3 of the last 5 predictions agree, with confidence ≥ 0.6.
- **Clear**: 15 consecutive NORMAL predictions.
- **Escalate only**: during a leak, severity can go up (minor → moderate → severe) but never down until resolved.
- **Alert cooldown**: the same alert isn't repeated within 30 s.

This is called **hysteresis**: it is harder to change state than to stay in it, so valves don't flap open/closed.

### 10.3 Leak severity and location
For each zone: `score = (flow − baseline flow)/baseline flow − (pressure − baseline pressure)/baseline pressure`
(a leak makes flow **rise** and pressure **fall**, so both increase the score).
`leak index = largest zone score`, and the **suspected zone** is the zone with that score.
Thresholds were learned from the data (33rd/66th percentiles of the leak index over all leak readings): **minor < 0.257 ≤ moderate < 1.201 ≤ severe**.
In the data: LeakEvent1 → index 1.26, zone C (large leak); LeakEvent2 → 0.24, zone B; LeakEvent3 → 0.29, zone B.

**Faulty-sensor identification**: the sensor with an impossible value or, if none, the sensor deviating most from its baseline.

### 10.4 Actions table

| Situation | Alert level | Command(s) |
|---|---|---|
| Leak, minor | WARNING | `set_pump pump1 85` (lower pressure → less water lost) |
| Leak, moderate | CRITICAL | `set_pump pump1 70` + `isolate_zone <zone>` |
| Leak, severe | CRITICAL | `set_pump pump1 50` + `isolate_zone <zone>` |
| Sensor fault | WARNING | `flag_sensor <sensor>`, deliberately **no** valve/pump action |
| Cyber-attack | CRITICAL | `safe_mode true` (controller ignores remote setpoints) |
| Back to normal | INFO | `restore` |
| Pump uses much more speed per unit flow than usual | WARNING | advisory only: "check pump wear or a closed valve" |

**Auto mode OFF** (dashboard switch): the engine sends no commands, only "Recommended action: …" alerts. A human operator decides (human-in-the-loop).

### 10.5 Live configuration
The dashboard publishes to `wds/engine/config`, and the engine applies it immediately:
change model, auto mode, minimum confidence, votes required, reset KPIs. The engine answers on `wds/engine/config/current` (retained), so the dashboard always shows the real settings.

---

## 11. Performance evaluation (the 3 boxes in the diagram)

| Diagram box | What we measure | Where |
|---|---|---|
| **ML performance** | accuracy, precision, recall, F1, false alarms, confusion matrix; offline **and** live on the stream | `ml/results/`, dashboard *Performance* |
| **Computational performance** | training time, inference latency, memory, model size, CPU | `ml/results/report.md`, live engine CPU/RAM |
| **Closed-loop performance** | control effectiveness, response time, end-to-end latency, stability, savings | `engine/kpi.py` → `wds/engine/kpi` |

Closed-loop KPIs from one full playlist run:

| KPI | Value | Meaning |
|---|---|---|
| Online accuracy | 99.85 % | live predictions vs ground truth |
| Events true / detected / missed | 8 / 8 / 0 | every real event was caught |
| Detection delay | ~4 readings | onset → confirmed (voting costs ≥ 3 readings) |
| Response time | ~309 ms | command → actuator ack |
| End-to-end latency | ~360 ms | sensor reading → actuator ack |
| Leaks contained / attacks blocked | 3 / 1 | **control effectiveness** |
| Unnecessary actions | corrective commands while truly normal | **stability** |
| Water saved | excess flow × readings skipped after isolation | **resource saving** (dataset flow units × s) |

---

## 12. Database (`db/schema.sql`)

| Table | Written by | Contents |
|---|---|---|
| `telemetry` | Node-RED | every reading + received time + quality + ground truth |
| `predictions` | Node-RED (from the engine's MQTT messages) | class, confidence, state, severity, zone, latency |
| `alerts` | Node-RED | all alerts from the engine and the edge rule |
| `actions` | Node-RED | every command + ack time + response ms + effect |
| `kpi_log` | Node-RED | KPI snapshot every 10 s (JSON) |

**WAL mode** (`PRAGMA journal_mode = WAL`) lets Node-RED write while Python reads at the same time without locking errors.
Node-RED owns all writes; Python only reads `telemetry`. Responsibilities are clearly separated.

---

## 13. Node-RED flows (what's in the editor at http://localhost:1880)

| Flow tab | Nodes and purpose |
|---|---|
| **1-3 Ingest** | `mqtt in` telemetry → *Validate & enrich* (edge rule) → *Store telemetry* → link to dashboard; simulator-status tracker |
| **3 Dashboard: Live Monitoring** | *Split to widgets* → charts, gauges, templates |
| **5-8 Analytics, Alerts & Actions** | predictions → store + state card + probability bars + timeline; alerts → store + table + pop-up toast; commands and acks → `actions` table + action log + actuator panel |
| **Control Panel** | switch / dropdown / sliders → `wds/engine/config`; override buttons → *build command* → `wds/control/cmd`; scenario buttons → `wds/sim/control`; clear database |
| **Performance Evaluation** | KPI table; *read metrics.json* → model comparison table + plots; row counts every 5 s |

Node types used: `mqtt in`, `mqtt out`, `function`, `inject`, `link in/out`, `file in`, `json`, `debug`, `comment`, and dashboard `ui_chart`, `ui_gauge`, `ui_template`, `ui_button`, `ui_switch`, `ui_slider`, `ui_dropdown`, `ui_toast`.

The database connection is created once in `node-red/settings.js` and shared via `functionGlobalContext`, so every function node can call `global.get('db')`.

---

## 14. Dashboard pages (http://localhost:1880/ui)

1. **Live Monitoring**: pressure and flow charts (last 2 min), 4 pressure gauges, pump and valve positions, device info, data quality, MQTT transit time.
2. **Analytics & Alerts**: big coloured system-state card (green NORMAL / red LEAK / orange SENSOR FAULT / purple CYBER), class probabilities, predicted vs actual timeline, leak index, alert table, actuator panel (valves OPEN/CLOSED, mode, pump setpoints, flagged sensors), action log with ack times.
3. **Control Panel**: auto mode, model selector, sliders, manual override, scenario injection, replay speed, clear database.
4. **Performance**: live closed-loop KPIs, offline model comparison table + plots, database row counts.

---

## 15. The simulator (`simulator/iot_node.py`) in detail

**Sensor side**
- Plays the **playlist** from `config.json`: clean → leak1 → clean → sensor23 → leak2 → cyber → leak3 → sensor45, then loops.
- Each item gets a new `segment` number.
- `--rate` sets readings per second; `--scenario leak1` loops a single recording.
- Listens on `wds/sim/control` for dashboard commands: play a scenario now, change speed, pause, resume, next.

**Actuator side**
- State: mode (AUTO/SAFE), pump setpoints (%), 4 isolation valves (OPEN/CLOSED), flagged sensors.
- Commands: `set_pump`, `isolate_zone`, `open_valve`, `close_valve`, `safe_mode`, `flag_sensor`, `unflag_sensor`, `restore`.
- Every command → 300 ms delay → state change → acknowledgement.

**Closed-loop approximation (be honest about this in the viva)**
Recorded data can't react to a valve. So:
- If a valve is closed **while a leak is playing**, after 2 s the rest of the leak is skipped (`leak_isolated`).
- If safe mode is switched on **during an attack**, the rest of the attack is skipped (`attack_blocked`).
- Pump changes and sensor flags are recorded but don't change the data.

With a real physical network (or a hydraulic simulator such as EPANET/WNTR), the actuator code would stay the same and only the data source would change.

---

## 16. How to run and demo

```bash
powershell -ExecutionPolicy Bypass -File start_all.ps1 -Rate 5
```
Or in 4 terminals: `node broker/broker.js` → `cd node-red && node-red --userDir . --settings settings.js` → `python -m engine.decision_engine` → `python -m simulator.iot_node --rate 5`.

### Suggested 5-minute demo script
1. **Live Monitoring**: "Readings arrive over MQTT, Node-RED validates and stores them in SQLite, and the charts update live."
2. **Control Panel → Leak 1 (large)**, then switch to **Analytics**: the state turns red (LEAK, MODERATE → SEVERE, zone C), alerts pop up, the Zone C valve closes, the action log shows ~300 ms acks; after the leak is contained it returns to NORMAL and `restore` is sent.
3. **Sensor fault P2**: orange SENSOR FAULT on `p2_out`, sensor flagged, *no* valve closed: "We avoid unnecessary switching for a sensor problem."
4. **Cyber-attack**: purple state, SAFE mode, attack blocked.
5. **Turn Auto mode OFF** and inject a leak: only "Recommended action" alerts appear, the operator decides.
6. **Switch the model to naive_bayes**: false leak alarms appear. Switch back to extra_trees: "This is why we compared 9 models and deployed the best by macro F1."
7. **Performance page**: live KPIs and the offline comparison table.
8. Optionally open the Node-RED editor and the SQLite DB to show the stored rows.

---

## 17. Live modification during the viva (where to change what)

| Request from the examiner | What to change |
|---|---|
| "Make detection more / less sensitive" | Control Panel sliders, or `config.json → decision.votes_required / min_confidence` |
| "Isolate even minor leaks" | `config.json → decision.isolate_from_severity: "minor"` |
| "Reduce the pump less" | `config.json → decision.pump_reduction` |
| "Restore faster" | `decision.normal_to_restore` (e.g. 15 → 5) |
| "Change the valid pressure range" | `decision.pressure_valid_range` (used by the engine **and** Node-RED; restart Node-RED) |
| "Add a new alert in Node-RED" | edit function node *Validate & enrich* (flow *1-3 Ingest*) and click **Deploy** |
| "Add a new dashboard widget" | drag a `ui_gauge` / `ui_text` into flow *3 Dashboard*, wire it from *Split to widgets*, Deploy |
| "Add a new actuator command" | `execute_command()` in `simulator/iot_node.py` + a button in the Control Panel flow |
| "Use another ML model" | Control Panel dropdown, or `python -m engine.decision_engine --model random_forest` |
| "Add a new model" | add it to `sklearn_models()` in `ml/train.py`, run `python -m ml.train` |
| "Faster replay" | Control Panel speed slider, or `--rate 10` |
| "Show stored data" | open `data/wds.db` with DB Browser for SQLite, or run `python -c "from common import db; print(db.connect().execute('select count(*) from telemetry').fetchone()[0])"` |

After changing `config.json`, restart the affected program (the engine, and Node-RED for the edge rule).

---

## 18. Likely viva questions with short answers

**Why MQTT and not HTTP?**
MQTT is lightweight (2-byte header), publish/subscribe (devices don't need to know who reads their data), keeps one persistent connection, and has QoS levels, retained messages and Last Will. HTTP is request/response and heavier for many small, frequent messages.

**What is the role of the broker?**
It receives every published message and forwards it to all clients subscribed to that topic. It decouples senders from receivers.

**Why Node-RED?**
Visual, low-code integration: wire MQTT, a database and a dashboard together quickly, with function nodes for custom logic. It is good for rapid IoT prototyping and easy to change live.

**Why SQLite?**
Zero configuration, one file, enough for one site's data rate. WAL mode allows simultaneous reading and writing. MySQL would be used for multi-user or networked deployments.

**Why does the ML read from the database instead of directly from MQTT?**
To follow the given architecture (DB → Analytics). It also means every reading is stored first, the ML can be restarted without data loss, and the history needed for features comes from one source of truth.

**Why not act on a single prediction?**
Sensor noise can cause a single wrong prediction. Voting (3 of 5) and the 15-reading restore hold-off add hysteresis, which prevents valves switching repeatedly. The cost is a delay of ~3 readings, which we measured.

**Why no valve action for sensor faults?**
The pipes are fine and only the measurement is wrong. Closing valves would interrupt supply for no reason (unnecessary switching). Instead we flag the sensor for maintenance.

**What is leak severity based on?**
The leak index: relative flow increase plus relative pressure drop vs a baseline of normal readings, with thresholds learned from the dataset's percentiles.

**How do you know the location?**
The zone whose pressure fell and flow rose the most. It is a heuristic because the dataset doesn't record the true leak position.

**What is overfitting and how did you avoid it?**
A model memorising training data and failing on new data. We used a block-wise split (no near-duplicate leakage), ensembles (averaging reduces variance), `min_samples_leaf=2`, dropout in the MLP, and we compare against a held-out test set.

**Accuracy is 99.9 %; is that suspicious?**
The classes have very distinct signatures (fault values like −12 bar, attack valve at 100 %), the features include temporal context, and the block split prevents the usual leakage. We report macro F1 and per-class recall too, not just accuracy, and live accuracy on the stream was also 99.85 %.

**Difference between bagging and boosting?**
Bagging (Random Forest, Extra Trees) trains trees independently on random samples and averages them, which reduces variance. Boosting (Gradient Boosting) trains trees one after another, each correcting the previous errors, which reduces bias.

**What does the LSTM add?**
It learns from the sequence of the last 30 raw readings, i.e. it learns temporal patterns automatically instead of using hand-made features. It is also the fastest model at inference (0.8 ms).

**What is end-to-end latency here?**
From the sensor timestamp of the reading that confirmed the event to the actuator's acknowledgement: ≈ 360 ms (MQTT → Node-RED → SQLite → ML → decision → MQTT → actuator, including the 300 ms actuator delay).

**What happens if the IoT node disconnects?**
Its Last Will message `{"online": false}` is published by the broker, and the dashboard shows OFFLINE.

**What happens if the engine is down?**
Node-RED still stores data, the dashboard still shows live values, and the Node-RED edge rule still raises alerts for impossible readings. When the engine returns, it continues from the newest data.

**Advanced features implemented (extra marks)**
- 4-class detection (leak, sensor fault, cyber-attack), not just leak yes/no
- leak severity grading + zone localisation + faulty-sensor identification
- hybrid ML + physics rule
- 9 models across 3 families with full ML/computational comparison
- hysteresis / voting for stability
- automatic closed-loop control with acknowledgements and measured latency
- live model switching, auto/manual (human-in-the-loop) mode
- scenario injection for demos
- live closed-loop KPIs
- edge processing in Node-RED
- MQTT QoS, retained messages and Last Will

---

## 19. Limitations and future work
- Recorded data can't respond to actions. A hydraulic simulator (EPANET/WNTR) or real hardware would give true closed-loop behaviour.
- Leak location is a heuristic; more sensors or labelled leak positions would allow ML-based localisation.
- No tank-level sensor in the dataset.
- Security: the broker allows anonymous clients. In production, use MQTT username/password, TLS (port 8883) and access-control lists.
- The models are trained on one testbed; a new network would need retraining or transfer learning.
