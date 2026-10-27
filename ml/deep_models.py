"""PyTorch deep-learning models.

* FeatureMLP  - fully connected network on the engineered feature vector
* CNNLSTM     - 1D convolution + LSTM on the raw sensor sequence (last `seq_len`
                readings), so it learns temporal patterns without hand-made features
"""
import time

import numpy as np
import torch
from torch import nn


class FeatureMLP(nn.Module):
    def __init__(self, n_in, n_classes):
        super().__init__()
        self.net = nn.Sequential(
            nn.Linear(n_in, 128), nn.ReLU(), nn.Dropout(0.2),
            nn.Linear(128, 64), nn.ReLU(),
            nn.Linear(64, n_classes),
        )

    def forward(self, x):
        return self.net(x)


class CNNLSTM(nn.Module):
    def __init__(self, n_sensors, n_classes, hidden=64):
        super().__init__()
        self.conv = nn.Sequential(nn.Conv1d(n_sensors, 32, kernel_size=3, padding=1), nn.ReLU())
        self.lstm = nn.LSTM(32, hidden, batch_first=True)
        self.head = nn.Linear(hidden, n_classes)

    def forward(self, x):  # x: (batch, seq_len, n_sensors)
        z = self.conv(x.transpose(1, 2)).transpose(1, 2)
        _, (h, _) = self.lstm(z)
        return self.head(h[-1])


def build(kind, n_in, n_classes):
    if kind == "mlp":
        return FeatureMLP(n_in, n_classes)
    if kind == "cnn_lstm":
        return CNNLSTM(n_in, n_classes)
    raise ValueError(kind)


def fit(model, X, y, n_classes, epochs=20, batch_size=256, lr=1e-3, seed=42):
    """Train with class-balanced cross-entropy; returns training time in seconds."""
    torch.manual_seed(seed)
    counts = np.bincount(y, minlength=n_classes).astype(np.float32)
    weights = torch.tensor(counts.sum() / (n_classes * np.maximum(counts, 1)))
    loss_fn = nn.CrossEntropyLoss(weight=weights)
    opt = torch.optim.Adam(model.parameters(), lr=lr)
    Xt, yt = torch.tensor(X, dtype=torch.float32), torch.tensor(y, dtype=torch.long)
    t0 = time.perf_counter()
    model.train()
    for epoch in range(epochs):
        perm = torch.randperm(len(Xt))
        total = 0.0
        for i in range(0, len(Xt), batch_size):
            idx = perm[i:i + batch_size]
            opt.zero_grad()
            loss = loss_fn(model(Xt[idx]), yt[idx])
            loss.backward()
            opt.step()
            total += loss.item() * len(idx)
        if (epoch + 1) % 5 == 0:
            print(f"      epoch {epoch + 1:3d}/{epochs}  loss={total / len(Xt):.4f}")
    model.eval()
    return time.perf_counter() - t0


@torch.no_grad()
def predict_proba(model, X):
    return torch.softmax(model(torch.tensor(X, dtype=torch.float32)), dim=1).numpy()
