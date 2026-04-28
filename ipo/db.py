import os
import shutil
import sqlite3
from datetime import datetime
from flask import g, current_app

SCHEMA = """
CREATE TABLE IF NOT EXISTS participants (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    securities_account TEXT,
    status TEXT NOT NULL DEFAULT 'active',
    note TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS advances (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    participant_id INTEGER NOT NULL REFERENCES participants(id),
    amount REAL NOT NULL,
    kind TEXT NOT NULL,
    settlement_id INTEGER REFERENCES settlements(id),
    date TEXT NOT NULL,
    note TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS idx_advances_participant ON advances(participant_id);
CREATE INDEX IF NOT EXISTS idx_advances_settlement ON advances(settlement_id);

CREATE TABLE IF NOT EXISTS projects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT NOT NULL,
    name TEXT NOT NULL,
    listing_date TEXT,
    note TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS subscriptions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL REFERENCES projects(id),
    participant_id INTEGER NOT NULL REFERENCES participants(id),
    lots INTEGER NOT NULL DEFAULT 0,
    cost REAL NOT NULL DEFAULT 0,
    sell_revenue REAL,
    sell_date TEXT,
    status TEXT NOT NULL DEFAULT 'open',
    settlement_id INTEGER REFERENCES settlements(id),
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    UNIQUE(project_id, participant_id)
);
CREATE INDEX IF NOT EXISTS idx_subs_project ON subscriptions(project_id);
CREATE INDEX IF NOT EXISTS idx_subs_participant ON subscriptions(participant_id);
CREATE INDEX IF NOT EXISTS idx_subs_settlement ON subscriptions(settlement_id);
CREATE INDEX IF NOT EXISTS idx_subs_sell_date ON subscriptions(sell_date);

CREATE TABLE IF NOT EXISTS settlements (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    month TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'settled',
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    reversed_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_settlements_month ON settlements(month);

CREATE TABLE IF NOT EXISTS settlement_lines (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    settlement_id INTEGER NOT NULL REFERENCES settlements(id),
    participant_id INTEGER NOT NULL REFERENCES participants(id),
    net_profit REAL NOT NULL,
    share REAL NOT NULL,
    advance_before REAL NOT NULL,
    deducted REAL NOT NULL,
    advance_after REAL NOT NULL,
    payout REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_lines_settlement ON settlement_lines(settlement_id);
"""


def get_db():
    if "db" not in g:
        conn = sqlite3.connect(
            current_app.config["DATABASE"],
            detect_types=sqlite3.PARSE_DECLTYPES,
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        g.db = conn
    return g.db


def close_db(_=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db(app):
    db_path = app.config["DATABASE"]
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA)
    conn.commit()
    conn.close()


def backup_db():
    src = current_app.config["DATABASE"]
    if not os.path.exists(src):
        return None
    backup_dir = current_app.config["BACKUP_DIR"]
    os.makedirs(backup_dir, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S_%f")
    dst = os.path.join(backup_dir, f"portfolio_{stamp}.db")
    db = get_db()
    db.commit()
    shutil.copy2(src, dst)
    return dst
