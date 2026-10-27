"""Feature engineering shared by offline training and the online decision engine.

`make_features` only looks backwards in time (rolling windows), so computing it
on the last N rows from the database gives exactly the same values the model
saw during training.
"""
import numpy as np
import pandas as pd

from .config import FLOWS, PRESSURES, SENSORS, ZONES

EPS = 1e-6


def make_features(df, short_window=10, baseline_window=60):
    """Turn consecutive raw readings into a feature matrix (one row per reading)."""
    raw = df[SENSORS].astype(float)
    feats = {c: raw[c] for c in SENSORS}
    for c in PRESSURES + FLOWS:
        s = raw[c]
        feats[f"{c}_mean{short_window}"] = s.rolling(short_window, min_periods=1).mean()
        feats[f"{c}_std{short_window}"] = s.rolling(short_window, min_periods=1).std().fillna(0.0)
        feats[f"{c}_dev{baseline_window}"] = s - s.rolling(baseline_window, min_periods=1).mean()
        feats[f"{c}_diff"] = s.diff().fillna(0.0)
    # simple hydraulic indicators
    feats["head_1_2"] = raw["p1_out"] - raw["p2_out"]
    feats["head_4_3"] = raw["p4_in"] - raw["p3_in"]
    feats["min_pressure"] = raw[PRESSURES].min(axis=1)
    feats["flow_out_total"] = raw[["f2", "f3", "f4"]].sum(axis=1)
    feats["pump1_per_flow"] = raw["vfd1"] / (raw["f1"] + 0.05)
    feats["valve_diff"] = raw["valve1"] - raw["valve2"]
    return pd.DataFrame(feats, index=df.index)


def feature_names(short_window=10, baseline_window=60):
    dummy = pd.DataFrame(np.zeros((2, len(SENSORS))), columns=SENSORS)
    return list(make_features(dummy, short_window, baseline_window).columns)


def make_sequences(raw_values, seq_len):
    """Sliding windows of shape (n, seq_len, n_sensors) ending at every row.
    The first rows are padded by repeating the first reading."""
    raw_values = np.asarray(raw_values, dtype=np.float32)
    pad = np.repeat(raw_values[:1], seq_len - 1, axis=0)
    padded = np.concatenate([pad, raw_values], axis=0)
    idx = np.arange(seq_len)[None, :] + np.arange(len(raw_values))[:, None]
    return padded[idx]


# ---------------------------------------------------------------------------
# Physics-based helpers used for leak severity / localisation and sensor checks
# ---------------------------------------------------------------------------

def zone_leak_scores(row, baseline):
    """Per-zone leak score: relative flow increase + relative pressure drop
    compared with a baseline of normal operation. Higher = more leak-like."""
    scores = {}
    for zone, z in ZONES.items():
        p, f = z["pressure"], z["flow"]
        dp = (row[p] - baseline[p]) / (abs(baseline[p]) + 0.1)
        df = (row[f] - baseline[f]) / (abs(baseline[f]) + 0.05)
        scores[zone] = float(df - dp)
    return scores


def leak_index(row, baseline):
    """Overall leak magnitude = strongest zone score (0 when nothing changed)."""
    return max(0.0, max(zone_leak_scores(row, baseline).values()))


def severity_from_index(index, thresholds):
    """Map a leak index onto minor / moderate / severe using learned thresholds."""
    if index >= thresholds["severe"]:
        return "severe"
    if index >= thresholds["moderate"]:
        return "moderate"
    return "minor"


def invalid_sensors(row, pressure_range, flow_range):
    """Sensors whose reading is physically impossible (e.g. negative pressure)."""
    bad = []
    for c in PRESSURES:
        if not pressure_range[0] <= row[c] <= pressure_range[1]:
            bad.append(c)
    for c in FLOWS:
        if not flow_range[0] <= row[c] <= flow_range[1]:
            bad.append(c)
    return bad
