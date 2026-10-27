"""Uniform inference wrapper around every trained model.

The decision engine hands over the most recent raw readings (a DataFrame read
from SQLite) and gets class probabilities back, regardless of whether the model
is scikit-learn on engineered features or a PyTorch sequence model.
"""
import json
import os

import joblib
import numpy as np

from .config import SENSORS, abspath
from .features import make_features, make_sequences

MODELS_DIR = abspath("ml/models")


def load_meta(models_dir=MODELS_DIR):
    with open(os.path.join(models_dir, "meta.json"), "r", encoding="utf-8") as f:
        return json.load(f)


class Predictor:
    def __init__(self, name, models_dir=MODELS_DIR, meta=None):
        self.meta = meta or load_meta(models_dir)
        if name not in self.meta["models"]:
            raise KeyError(f"unknown model '{name}', available: {list(self.meta['models'])}")
        self.name = name
        info = self.meta["models"][name]
        self.family = info["family"]
        self.input = info["input"]
        path = os.path.join(models_dir, info["file"])
        if info["file"].endswith(".pt"):
            import torch
            from ml import deep_models
            ckpt = torch.load(path, map_location="cpu", weights_only=False)
            self.torch_model = deep_models.build(ckpt["arch"], ckpt["n_in"], ckpt["n_classes"])
            self.torch_model.load_state_dict(ckpt["state_dict"])
            self.torch_model.eval()
            self.mean, self.std = np.array(ckpt["mean"]), np.array(ckpt["std"])
            self.sk_model = None
        else:
            self.sk_model = joblib.load(path)
            self.torch_model = None

    @property
    def history_needed(self):
        """How many past readings must be supplied for exact features."""
        return max(self.meta["baseline_window"], self.meta["seq_len"])

    def predict_proba(self, raw_df, last_n=1):
        """Probabilities (last_n x n_classes) for the final `last_n` rows of raw_df."""
        if self.input == "sequence":
            raw = (raw_df[SENSORS].to_numpy(dtype=np.float32) - self.mean) / self.std
            X = make_sequences(raw, self.meta["seq_len"])[-last_n:]
        else:
            feats = make_features(raw_df, self.meta["short_window"], self.meta["baseline_window"])
            X = feats.iloc[-last_n:].to_numpy(dtype=np.float32)
            if self.torch_model is not None:
                X = (X - self.mean) / self.std
        if self.torch_model is not None:
            from ml import deep_models
            return deep_models.predict_proba(self.torch_model, X)
        return self.sk_model.predict_proba(X)
