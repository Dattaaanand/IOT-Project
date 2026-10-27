-- Smart Water Distribution - SQLite schema
-- Created by Node-RED on start-up and by `python -m common.db` (both idempotent).
PRAGMA journal_mode = WAL;

-- every sensor reading received from the IoT node (written by Node-RED)
CREATE TABLE IF NOT EXISTS telemetry (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          INTEGER NOT NULL,          -- sensor timestamp (ms since epoch)
    received_ts INTEGER NOT NULL,          -- when Node-RED received it
    device_id   TEXT,
    seq         INTEGER,
    scenario    TEXT,                      -- recording being replayed
    segment     INTEGER,                   -- replay segment (continuous piece of data)
    row_idx     INTEGER,
    p1_out REAL, p2_out REAL, p3_in REAL, p4_in REAL,
    f1 REAL, f2 REAL, f3 REAL, f4 REAL,
    vfd1 REAL, vfd2 REAL, vfd3 REAL, vfd4_1 REAL, vfd4_2 REAL,
    valve1 REAL, valve2 REAL,
    gt_class    INTEGER,                   -- ground truth (evaluation only, never used by the model)
    quality     TEXT                       -- Node-RED range check: OK / SUSPECT
);
CREATE INDEX IF NOT EXISTS idx_telemetry_segment ON telemetry(segment, id);

-- one ML classification per reading (published by the decision engine)
CREATE TABLE IF NOT EXISTS predictions (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    ts           INTEGER NOT NULL,
    telemetry_id INTEGER,
    telemetry_ts INTEGER,
    model        TEXT,
    pred_class   INTEGER,
    pred_name    TEXT,
    confidence   REAL,
    state        TEXT,                     -- confirmed system state after voting
    severity     TEXT,
    zone         TEXT,
    leak_index   REAL,
    gt_class     INTEGER,
    latency_ms   REAL
);
CREATE INDEX IF NOT EXISTS idx_predictions_ts ON predictions(ts);

-- alerts raised by the decision engine or by Node-RED edge rules
CREATE TABLE IF NOT EXISTS alerts (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    ts       INTEGER NOT NULL,
    source   TEXT,
    level    TEXT,                         -- INFO / WARNING / CRITICAL
    type     TEXT,
    message  TEXT,
    severity TEXT,
    zone     TEXT,
    sensor   TEXT
);

-- control commands and their acknowledgement by the actuator node
CREATE TABLE IF NOT EXISTS actions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    cmd_id      TEXT,
    ts          INTEGER NOT NULL,          -- command issued
    source      TEXT,                      -- engine / dashboard
    action      TEXT,
    target      TEXT,
    value       TEXT,
    reason      TEXT,
    ack_ts      INTEGER,                   -- actuator acknowledgement
    response_ms INTEGER,
    effect      TEXT
);
CREATE INDEX IF NOT EXISTS idx_actions_cmd ON actions(cmd_id);

-- periodic snapshot of closed-loop KPIs
CREATE TABLE IF NOT EXISTS kpi_log (
    id   INTEGER PRIMARY KEY AUTOINCREMENT,
    ts   INTEGER NOT NULL,
    data TEXT                              -- JSON
);
