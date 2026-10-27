"""Train and compare traditional ML, ensemble ML and deep-learning models.

    python -m ml.train            # train everything, write ml/models + ml/results

Task: 4-class classification of every sensor reading into
NORMAL / LEAK / SENSOR_FAULT / CYBER_ATTACK.

Evaluation (results/metrics.json, results/report.md):
  * ML performance          accuracy, precision, recall, F1 (macro + per class),
                            false-alarm rate, confusion matrix
  * Computational           training time, CPU use, memory, model size,
                            single-reading inference latency (incl. features)
"""
import json
import os
import time

import joblib
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import psutil
import torch
from sklearn.ensemble import ExtraTreesClassifier, HistGradientBoostingClassifier, RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, confusion_matrix, precision_recall_fscore_support
from sklearn.naive_bayes import GaussianNB
from sklearn.neighbors import KNeighborsClassifier
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.tree import DecisionTreeClassifier

from common.config import CLASS_NAMES, NORMAL, SENSORS, abspath, load_config
from common.dataset import load_all
from common.features import leak_index, make_features, make_sequences, zone_leak_scores
from common.predictor import Predictor
from ml import deep_models

MODELS_DIR = abspath("ml/models")
RESULTS_DIR = abspath("ml/results")
N_CLASSES = len(CLASS_NAMES)


def sklearn_models(seed):
    """name -> (family, estimator). Scale-sensitive models get a StandardScaler."""
    return {
        "logistic_regression": ("Traditional ML", make_pipeline(
            StandardScaler(), LogisticRegression(max_iter=3000, class_weight="balanced"))),
        "knn": ("Traditional ML", make_pipeline(
            StandardScaler(), KNeighborsClassifier(n_neighbors=7, weights="distance"))),
        "decision_tree": ("Traditional ML", DecisionTreeClassifier(
            max_depth=12, class_weight="balanced", random_state=seed)),
        "naive_bayes": ("Traditional ML", make_pipeline(StandardScaler(), GaussianNB())),
        "random_forest": ("Ensemble ML", RandomForestClassifier(
            n_estimators=150, min_samples_leaf=2, class_weight="balanced_subsample",
            n_jobs=-1, random_state=seed)),
        "extra_trees": ("Ensemble ML", ExtraTreesClassifier(
            n_estimators=150, min_samples_leaf=2, class_weight="balanced",
            n_jobs=-1, random_state=seed)),
        "gradient_boosting": ("Ensemble ML", HistGradientBoostingClassifier(
            max_iter=200, learning_rate=0.1, class_weight="balanced", random_state=seed)),
    }


# ---------------------------------------------------------------------------
# data preparation
# ---------------------------------------------------------------------------

def block_split(df, block_size, test_fraction, rng):
    """Split one recording into contiguous blocks and send a share of the blocks
    of every class to the test set. Contiguous blocks keep neighbouring (almost
    identical) readings on the same side of the split, unlike a random row split."""
    block = np.arange(len(df)) // block_size
    major = df.groupby(block)["gt_class"].agg(lambda s: s.mode().iloc[0])
    test_blocks = []
    for _, blocks in major.groupby(major):
        ids = blocks.index.to_numpy()
        n_test = int(round(len(ids) * test_fraction))
        if len(ids) > 1:
            n_test = max(1, n_test)
        test_blocks.extend(rng.choice(ids, size=n_test, replace=False))
    return np.isin(block, test_blocks)


def prepare(cfg):
    m = cfg["ml"]
    rng = np.random.default_rng(m["random_state"])
    data = load_all(cfg["dataset_dir"])
    X, S, y, test, src = [], [], [], [], []
    for name, df in data.items():
        X.append(make_features(df, m["short_window"], m["baseline_window"]))
        S.append(make_sequences(df[SENSORS].to_numpy(), m["seq_len"]))
        y.append(df["gt_class"].to_numpy())
        test.append(block_split(df, m["block_size"], m["test_fraction"], rng))
        src.append(np.full(len(df), name))
    X = pd.concat(X, ignore_index=True)
    return data, X, np.concatenate(S), np.concatenate(y), np.concatenate(test), np.concatenate(src)


# ---------------------------------------------------------------------------
# evaluation helpers
# ---------------------------------------------------------------------------

def ml_metrics(y_true, y_pred):
    p, r, f, _ = precision_recall_fscore_support(y_true, y_pred, average="macro", zero_division=0)
    pc, rc, fc, sc = precision_recall_fscore_support(
        y_true, y_pred, labels=range(N_CLASSES), zero_division=0)
    normal = y_true == NORMAL
    return {
        "accuracy": accuracy_score(y_true, y_pred),
        "precision": p, "recall": r, "f1": f,
        "false_alarm_rate": float(np.mean(y_pred[normal] != NORMAL)) if normal.any() else 0.0,
        "per_class": {CLASS_NAMES[i]: {"precision": pc[i], "recall": rc[i], "f1": fc[i], "support": int(sc[i])}
                      for i in range(N_CLASSES)},
        "confusion_matrix": confusion_matrix(y_true, y_pred, labels=range(N_CLASSES)).tolist(),
    }


class ResourceMeter:
    """Wall time, CPU utilisation and resident-memory growth of a code block."""

    def __enter__(self):
        self.proc = psutil.Process()
        self.rss0 = self.proc.memory_info().rss
        self.cpu0 = sum(self.proc.cpu_times()[:2])
        self.t0 = time.perf_counter()
        return self

    def __exit__(self, *exc):
        self.seconds = time.perf_counter() - self.t0
        cpu = sum(self.proc.cpu_times()[:2]) - self.cpu0
        self.cpu_percent = 100.0 * cpu / max(self.seconds, 1e-9)  # >100% = several cores
        self.memory_mb = max(0, self.proc.memory_info().rss - self.rss0) / 2 ** 20


def inference_latency_ms(predictor, raw_window, repeats=100):
    """Average time to classify ONE new reading, including feature extraction,
    exactly as the online decision engine does it."""
    predictor.predict_proba(raw_window)  # warm-up
    t0 = time.perf_counter()
    for _ in range(repeats):
        predictor.predict_proba(raw_window)
    return 1000 * (time.perf_counter() - t0) / repeats


# ---------------------------------------------------------------------------
# leak severity / localisation (physics-based, thresholds learned from data)
# ---------------------------------------------------------------------------

def leak_analysis(data):
    """Leak index of every leak reading relative to the normal readings that
    precede the leak in the same recording; severity thresholds are the 33rd
    and 66th percentiles of that distribution."""
    indices, per_event = [], {}
    for name, df in data.items():
        if not name.startswith("leak"):
            continue
        onset = int(np.argmax(df["label"].to_numpy() == 1))
        baseline = df.iloc[:onset][SENSORS].median()
        leak_rows = df[df["label"] == 1]
        idx = [leak_index(r, baseline) for _, r in leak_rows.iterrows()]
        zones = pd.DataFrame([zone_leak_scores(r, baseline) for _, r in leak_rows.iterrows()]).mean()
        indices.extend(idx)
        per_event[name] = {"mean_leak_index": float(np.mean(idx)),
                           "suspected_zone": zones.idxmax(),
                           "zone_scores": zones.round(3).to_dict()}
    q = np.percentile(indices, [33, 66])
    return {"moderate": float(q[0]), "severe": float(q[1])}, per_event


# ---------------------------------------------------------------------------
# plots + report
# ---------------------------------------------------------------------------

def plot_confusions(results):
    n = len(results)
    cols = 3
    rows = int(np.ceil(n / cols))
    fig, axes = plt.subplots(rows, cols, figsize=(4.6 * cols, 4.2 * rows))
    short = ["Normal", "Leak", "Sensor", "Cyber"]
    for ax, (name, r) in zip(axes.flat, results.items()):
        cm = np.array(r["confusion_matrix"], dtype=float)
        cmn = cm / np.maximum(cm.sum(axis=1, keepdims=True), 1)
        ax.imshow(cmn, cmap="Blues", vmin=0, vmax=1)
        for i in range(N_CLASSES):
            for j in range(N_CLASSES):
                ax.text(j, i, int(cm[i, j]), ha="center", va="center",
                        color="white" if cmn[i, j] > 0.5 else "black", fontsize=9)
        ax.set_xticks(range(N_CLASSES), short, fontsize=8)
        ax.set_yticks(range(N_CLASSES), short, fontsize=8)
        ax.set_title(f"{name}\nF1={r['f1']:.3f}", fontsize=10)
        ax.set_xlabel("predicted", fontsize=8)
        ax.set_ylabel("actual", fontsize=8)
    for ax in list(axes.flat)[n:]:
        ax.axis("off")
    fig.tight_layout()
    fig.savefig(os.path.join(RESULTS_DIR, "confusion_matrices.png"), dpi=120)
    plt.close(fig)


def plot_comparison(results):
    names = list(results)
    colors = {"Traditional ML": "#4C78A8", "Ensemble ML": "#F58518", "Deep Learning": "#54A24B"}
    c = [colors[results[n]["family"]] for n in names]
    fig, axes = plt.subplots(1, 3, figsize=(16, 4.5))
    for ax, key, title in zip(axes, ["f1", "train_time_s", "latency_ms"],
                              ["Macro F1 (higher is better)", "Training time [s]", "Inference latency per reading [ms]"]):
        ax.bar(names, [results[n][key] for n in names], color=c)
        ax.set_title(title)
        ax.tick_params(axis="x", rotation=45)
        for lbl in ax.get_xticklabels():
            lbl.set_ha("right")
    handles = [plt.Rectangle((0, 0), 1, 1, color=v) for v in colors.values()]
    axes[0].legend(handles, colors.keys(), fontsize=8, loc="lower left")
    fig.tight_layout()
    fig.savefig(os.path.join(RESULTS_DIR, "model_comparison.png"), dpi=120)
    plt.close(fig)


def write_report(results, best, thresholds, leak_events, n_train, n_test):
    lines = ["# Model evaluation report", "",
             f"Train readings: {n_train}, test readings: {n_test} (contiguous-block split).", "",
             "## ML and computational performance", "",
             "| Model | Family | Accuracy | Precision | Recall | F1 | False-alarm rate | Train time [s] | CPU [%] | Memory [MB] | Size [KB] | Latency [ms] |",
             "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for n, r in results.items():
        lines.append(f"| {n}{' **(deployed)**' if n == best else ''} | {r['family']} | {r['accuracy']:.4f} | "
                     f"{r['precision']:.4f} | {r['recall']:.4f} | {r['f1']:.4f} | {r['false_alarm_rate']:.4f} | "
                     f"{r['train_time_s']:.2f} | {r['cpu_percent']:.0f} | {r['memory_mb']:.1f} | "
                     f"{r['model_size_kb']:.0f} | {r['latency_ms']:.2f} |")
    lines += ["", "## Per-class recall", "", "| Model | " + " | ".join(CLASS_NAMES) + " |",
              "|---" * (N_CLASSES + 1) + "|"]
    for n, r in results.items():
        lines.append(f"| {n} | " + " | ".join(f"{r['per_class'][c]['recall']:.3f}" for c in CLASS_NAMES) + " |")
    lines += ["", "## Leak severity thresholds (leak index)", "",
              f"* minor < {thresholds['moderate']:.3f} <= moderate < {thresholds['severe']:.3f} <= severe", "",
              "| Leak event | Mean leak index | Suspected zone | Zone scores |", "|---|---|---|---|"]
    for n, e in leak_events.items():
        lines.append(f"| {n} | {e['mean_leak_index']:.3f} | {e['suspected_zone']} | {e['zone_scores']} |")
    lines += ["", "![comparison](model_comparison.png)", "", "![confusion](confusion_matrices.png)", ""]
    with open(os.path.join(RESULTS_DIR, "report.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


# ---------------------------------------------------------------------------

def main():
    cfg = load_config()
    m = cfg["ml"]
    os.makedirs(MODELS_DIR, exist_ok=True)
    os.makedirs(RESULTS_DIR, exist_ok=True)
    torch.set_num_threads(max(1, os.cpu_count() // 2))

    print("Loading dataset and building features ...")
    data, X, S, y, test, src = prepare(cfg)
    Xtr, Xte = X[~test].to_numpy(np.float32), X[test].to_numpy(np.float32)
    ytr, yte = y[~test], y[test]
    print(f"  {len(X)} readings, {X.shape[1]} features, train={len(ytr)} test={len(yte)}")
    print("  class counts (train):", dict(zip(CLASS_NAMES, np.bincount(ytr, minlength=N_CLASSES))))

    thresholds, leak_events = leak_analysis(data)
    meta = {"short_window": m["short_window"], "baseline_window": m["baseline_window"],
            "seq_len": m["seq_len"], "classes": CLASS_NAMES, "feature_names": list(X.columns),
            "severity_thresholds": thresholds, "models": {}}

    # a real 60-reading window for latency measurement
    latency_window = data["leak1"].iloc[600:600 + max(m["baseline_window"], m["seq_len"])]

    results = {}

    def finish(name, family, file, input_kind, pred, meter):
        meta["models"][name] = {"file": file, "family": family, "input": input_kind}
        with open(os.path.join(MODELS_DIR, "meta.json"), "w") as f:
            json.dump(meta, f, indent=2)
        r = ml_metrics(yte, pred)
        r.update(family=family, train_time_s=meter.seconds, cpu_percent=meter.cpu_percent,
                 memory_mb=meter.memory_mb,
                 model_size_kb=os.path.getsize(os.path.join(MODELS_DIR, file)) / 1024,
                 latency_ms=inference_latency_ms(Predictor(name, MODELS_DIR, meta), latency_window))
        results[name] = r
        print(f"    acc={r['accuracy']:.4f}  F1={r['f1']:.4f}  FAR={r['false_alarm_rate']:.4f}  "
              f"train={r['train_time_s']:.1f}s  latency={r['latency_ms']:.2f}ms")

    for name, (family, model) in sklearn_models(m["random_state"]).items():
        print(f"  [{family}] {name}")
        with ResourceMeter() as meter:
            model.fit(Xtr, ytr)
        if hasattr(model, "n_jobs"):
            model.n_jobs = 1  # one reading at a time online: avoid thread start-up cost
        joblib.dump(model, os.path.join(MODELS_DIR, f"{name}.joblib"))
        finish(name, family, f"{name}.joblib", "features", model.predict(Xte), meter)

    # deep learning: standardise with training statistics
    for name, arch, Atr, Ate, input_kind in [
        ("mlp", "mlp", Xtr, Xte, "features"),
        ("cnn_lstm", "cnn_lstm", S[~test], S[test], "sequence"),
    ]:
        print(f"  [Deep Learning] {name}")
        axes = (0,) if Atr.ndim == 2 else (0, 1)
        mean, std = Atr.mean(axis=axes), Atr.std(axis=axes) + 1e-6
        net = deep_models.build(arch, Atr.shape[-1], N_CLASSES)
        with ResourceMeter() as meter:
            deep_models.fit(net, (Atr - mean) / std, ytr, N_CLASSES, epochs=25 if arch == "mlp" else 15)
        torch.save({"arch": arch, "n_in": Atr.shape[-1], "n_classes": N_CLASSES,
                    "state_dict": net.state_dict(), "mean": mean.tolist(), "std": std.tolist()},
                   os.path.join(MODELS_DIR, f"{name}.pt"))
        pred = deep_models.predict_proba(net, (Ate - mean) / std).argmax(axis=1)
        finish(name, "Deep Learning", f"{name}.pt", input_kind, pred, meter)

    # deploy the best F1; ties broken by lower latency
    best = max(results, key=lambda n: (round(results[n]["f1"], 3), -results[n]["latency_ms"]))
    meta["best_model"] = best
    with open(os.path.join(MODELS_DIR, "meta.json"), "w") as f:
        json.dump(meta, f, indent=2)

    out = {"generated": time.strftime("%Y-%m-%d %H:%M:%S"), "best_model": best,
           "n_train": int(len(ytr)), "n_test": int(len(yte)), "classes": CLASS_NAMES,
           "severity_thresholds": thresholds, "leak_events": leak_events, "models": results}
    with open(os.path.join(RESULTS_DIR, "metrics.json"), "w") as f:
        json.dump(out, f, indent=2, default=float)
    pd.DataFrame({n: {k: v for k, v in r.items() if not isinstance(v, (dict, list))}
                  for n, r in results.items()}).T.to_csv(os.path.join(RESULTS_DIR, "metrics.csv"))
    plot_confusions(results)
    plot_comparison(results)
    write_report(results, best, thresholds, leak_events, len(ytr), len(yte))
    print(f"\nBest model: {best}  (F1={results[best]['f1']:.4f})")
    print(f"Severity thresholds: {thresholds}")
    print(f"Results written to {RESULTS_DIR}")


if __name__ == "__main__":
    main()
