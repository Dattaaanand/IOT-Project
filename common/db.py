"""SQLite helpers.

    python -m common.db            # create tables if missing
    python -m common.db --reset    # delete all recorded data and start fresh
"""
import argparse
import os
import sqlite3

from .config import abspath, load_config

SCHEMA = abspath("db/schema.sql")


def db_path(cfg=None):
    return abspath((cfg or load_config())["db_path"])


def connect(path=None, readonly=False):
    path = path or db_path()
    if readonly:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
    else:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        conn = sqlite3.connect(path, timeout=5)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(path=None):
    with connect(path) as conn, open(SCHEMA, encoding="utf-8") as f:
        conn.executescript(f.read())


def reset_db(path=None):
    with connect(path) as conn:
        for table in ("telemetry", "predictions", "alerts", "actions", "kpi_log"):
            conn.execute(f"DELETE FROM {table}")
        conn.execute("DELETE FROM sqlite_sequence")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--reset", action="store_true", help="delete all rows")
    args = ap.parse_args()
    init_db()
    if args.reset:
        reset_db()
    print(f"database ready: {db_path()}")
