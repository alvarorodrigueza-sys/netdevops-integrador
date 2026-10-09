import sqlite3
from contextlib import contextmanager
from .config import DB_PATH

SCHEMA = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS devices (
    device TEXT PRIMARY KEY,
    vendor TEXT,
    host TEXT,
    hostname TEXT,
    model TEXT,
    version TEXT,
    last_seen REAL,
    status TEXT NOT NULL DEFAULT 'unknown'
);

CREATE TABLE IF NOT EXISTS interface_state (
    device TEXT NOT NULL,
    interface TEXT NOT NULL,
    admin_status TEXT,
    oper_status TEXT,
    speed_bps INTEGER,
    phys_address TEXT,
    ip_address TEXT,
    rx_bytes INTEGER,
    tx_bytes INTEGER,
    rx_errors INTEGER,
    tx_errors INTEGER,
    last_seen REAL,
    PRIMARY KEY (device, interface)
);

CREATE TABLE IF NOT EXISTS telemetry (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp REAL NOT NULL,
    device TEXT NOT NULL,
    hostname TEXT,
    interface TEXT NOT NULL,
    speed_bps INTEGER,
    interval_seconds REAL,
    delta_rx_bytes INTEGER,
    delta_tx_bytes INTEGER,
    traffic_bps REAL,
    utilization_pct REAL,
    rx_errors INTEGER,
    tx_errors INTEGER,
    threshold_exceeded INTEGER NOT NULL DEFAULT 0,
    counter_reset INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_telemetry_device_interface_time
ON telemetry(device, interface, timestamp);

CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    opened_at REAL NOT NULL,
    device TEXT NOT NULL,
    interface TEXT NOT NULL,
    utilization_pct REAL NOT NULL,
    threshold_pct REAL NOT NULL,
    status TEXT NOT NULL DEFAULT 'detected',
    action TEXT NOT NULL DEFAULT 'READ_ONLY_ALERT',
    closed_at REAL
);
"""

@contextmanager
def connection():
    con = sqlite3.connect(DB_PATH, timeout=30)
    con.row_factory = sqlite3.Row
    try:
        yield con
        con.commit()
    finally:
        con.close()

def init_db():
    with connection() as con:
        con.executescript(SCHEMA)
