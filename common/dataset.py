"""Loading the WDSEventDB Excel files into tidy DataFrames."""
import os

import pandas as pd

from .config import COLUMN_MAP, SCENARIOS, abspath, load_config

CACHE_DIR = abspath("data/cache")


def load_scenario(name, dataset_dir=None):
    """Return one recording as a DataFrame with short column names plus
    `label` (0/1 from the file) and `gt_class` (multi-class ground truth)."""
    if dataset_dir is None:
        dataset_dir = load_config()["dataset_dir"]
    rel, event_class = SCENARIOS[name]
    # Excel parsing is slow, so keep a CSV copy after the first read
    cache = os.path.join(CACHE_DIR, f"{name}.csv")
    if os.path.exists(cache):
        df = pd.read_csv(cache)
    else:
        df = pd.read_excel(os.path.join(abspath(dataset_dir), rel))
        os.makedirs(CACHE_DIR, exist_ok=True)
        df.to_csv(cache, index=False)
    df = df.rename(columns=COLUMN_MAP).rename(columns={"Labels": "label"})
    df["label"] = df["label"].astype(int)
    df["gt_class"] = df["label"] * event_class
    df["scenario"] = name
    return df.reset_index(drop=True)


def load_all(dataset_dir=None):
    """Dict of scenario name -> DataFrame for every file in the dataset."""
    return {name: load_scenario(name, dataset_dir) for name in SCENARIOS}
