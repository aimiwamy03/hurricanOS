"""SQLite is the source of truth. Tinybird is a copy for charts."""

from __future__ import annotations

import sqlite3
import threading
from typing import Any

from app.config import DATA_DIR

write_lock = threading.RLock()
_conn: sqlite3.Connection | None = None
_ready = False

COLUMNS: dict[str, tuple[str, ...]] = {
    "storm_updates": (
        "id", "ts_utc", "source_url", "summary", "phase_proposed", "advisory_json", "official_text", "official_url"
    ),
    "zips": ("zip", "area", "priority", "reason", "phase", "phase_locked", "updated_ts", "during_votes"),
    "stores": ("id", "name", "chain", "address_norm", "zip", "lat", "lng", "hours", "source_url", "found_ts", "phone"),
    "essentials": ("key", "name", "pictogram_path"),
    "shelf_reports": ("id", "ts_utc", "store_id", "zip", "essential_key", "verdict", "device", "ip"),
    "products": ("id", "essential_key", "chain", "product_id", "url", "title"),
    "stock_checks": (
        "id",
        "ts_utc",
        "store_id",
        "zip",
        "essential_key",
        "status",
        "price",
        "confidence",
        "level",
        "source_url",
        "price_flag",
        "confidence_why",
        "results",
        "matched",
        "available",
    ),
    "check_products": (
        "id", "check_id", "zip", "essential_key", "product_id", "name", "price", "unit_price",
        "in_stock", "availability", "url", "image",
    ),
    "forecasts": ("id", "ts_utc", "essential_key", "zip", "risk", "reason"),
    "official_links": ("id", "title", "url", "kind", "found_ts"),
    "agent_steps": ("step", "ts_utc", "phase", "action", "summary", "duration_ms"),
}

_PRIMARY = {"agent_steps": "step", "zips": "zip", "essentials": "key"}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS storm_updates (
  id INTEGER PRIMARY KEY,
  ts_utc TEXT NOT NULL,
  source_url TEXT,
  summary TEXT,
  phase_proposed TEXT,
  advisory_json TEXT
);
CREATE TABLE IF NOT EXISTS zips (
  zip TEXT PRIMARY KEY,
  area TEXT,
  priority INTEGER,
  reason TEXT,
  phase TEXT,
  phase_locked INTEGER DEFAULT 0,
  updated_ts TEXT,
  during_votes INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS stores (
  id INTEGER PRIMARY KEY,
  name TEXT,
  chain TEXT,
  address_norm TEXT,
  zip TEXT,
  lat REAL,
  lng REAL,
  hours TEXT,
  source_url TEXT,
  found_ts TEXT,
  UNIQUE(chain, address_norm)
);
CREATE TABLE IF NOT EXISTS essentials (
  key TEXT PRIMARY KEY,
  name TEXT,
  pictogram_path TEXT
);
CREATE TABLE IF NOT EXISTS products (
  id INTEGER PRIMARY KEY,
  essential_key TEXT,
  chain TEXT,
  product_id TEXT,
  url TEXT,
  title TEXT
);
CREATE TABLE IF NOT EXISTS stock_checks (
  id INTEGER PRIMARY KEY,
  ts_utc TEXT NOT NULL,
  store_id INTEGER,
  zip TEXT,
  essential_key TEXT,
  status TEXT,
  price REAL,
  confidence REAL,
  level TEXT,
  source_url TEXT,
  price_flag INTEGER DEFAULT 0
);
-- The individual products behind one stock check (the ones that matched the essential).
CREATE TABLE IF NOT EXISTS check_products (
  id INTEGER PRIMARY KEY,
  check_id INTEGER,
  zip TEXT,
  essential_key TEXT,
  product_id TEXT,
  name TEXT,
  price REAL,
  unit_price TEXT,
  in_stock INTEGER,
  availability TEXT,
  url TEXT,
  image TEXT
);
CREATE INDEX IF NOT EXISTS idx_check_products_check ON check_products(check_id);
CREATE TABLE IF NOT EXISTS forecasts (
  id INTEGER PRIMARY KEY,
  ts_utc TEXT NOT NULL,
  essential_key TEXT,
  zip TEXT,
  risk TEXT,
  reason TEXT
);
-- What people standing in a store saw on the shelf. verdict: has | empty.
-- device is a hash of a random id the browser keeps; ip is only for rate limits.
CREATE TABLE IF NOT EXISTS shelf_reports (
  id INTEGER PRIMARY KEY,
  ts_utc TEXT,
  store_id INTEGER,
  zip TEXT,
  essential_key TEXT,
  verdict TEXT,
  device TEXT,
  ip TEXT
);
CREATE INDEX IF NOT EXISTS idx_shelf_reports_store ON shelf_reports(store_id, essential_key, ts_utc);
CREATE TABLE IF NOT EXISTS official_links (
  id INTEGER PRIMARY KEY,
  title TEXT,
  url TEXT,
  kind TEXT,
  found_ts TEXT
);
CREATE TABLE IF NOT EXISTS agent_steps (
  step INTEGER PRIMARY KEY,
  ts_utc TEXT,
  phase TEXT,
  action TEXT,
  summary TEXT,
  duration_ms INTEGER
);
CREATE TABLE IF NOT EXISTS kv (
  key TEXT PRIMARY KEY,
  value TEXT
);
CREATE INDEX IF NOT EXISTS idx_stock_checks_zip ON stock_checks(zip, essential_key, ts_utc);
CREATE INDEX IF NOT EXISTS idx_stock_checks_store ON stock_checks(store_id, essential_key, ts_utc);
CREATE INDEX IF NOT EXISTS idx_forecasts_zip ON forecasts(zip, ts_utc);
"""


def connect() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        _conn = sqlite3.connect(DATA_DIR / "app.db", check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA journal_mode=WAL;")
        _conn.execute("PRAGMA foreign_keys=ON;")
    return _conn


def init_db() -> None:
    global _ready
    with write_lock:
        if _ready:
            return
        connect().executescript(_SCHEMA)
        _migrate()
        _ready = True


def _migrate() -> None:
    """Columns added after the first run. ALTER TABLE only adds what is missing."""
    added = [
        ("stock_checks", "price_flag", "INTEGER DEFAULT 0"),
        ("zips", "during_votes", "INTEGER DEFAULT 0"),
        ("storm_updates", "official_text", "TEXT"),
        ("storm_updates", "official_url", "TEXT"),
        ("stock_checks", "confidence_why", "TEXT"),
        ("stores", "phone", "TEXT"),
        # How many products the search returned, how many were this essential, how many available.
        ("stock_checks", "results", "INTEGER"),
        ("stock_checks", "matched", "INTEGER"),
        ("stock_checks", "available", "INTEGER"),
    ]
    for table, column, kind in added:
        have = {row["name"] for row in connect().execute(f"PRAGMA table_info({table})").fetchall()}
        if column not in have:
            connect().execute(f"ALTER TABLE {table} ADD COLUMN {column} {kind}")
            connect().commit()


def execute(sql: str, params: tuple | dict = ()) -> sqlite3.Cursor:
    with write_lock:
        init_db()
        cursor = connect().execute(sql, params)
        connect().commit()
        return cursor


def query(sql: str, params: tuple | dict = ()) -> list[dict[str, Any]]:
    init_db()
    rows = connect().execute(sql, params).fetchall()
    return [dict(row) for row in rows]


def insert(table: str, row: dict) -> int:
    if table not in COLUMNS:
        raise ValueError(f"unknown table {table}")
    pk = _PRIMARY.get(table, "id")
    allowed = [name for name in COLUMNS[table] if name in row and name != pk]
    if not allowed:
        raise ValueError(f"no columns for {table}")
    placeholders = ", ".join("?" for _ in allowed)
    sql = f"INSERT INTO {table} ({', '.join(allowed)}) VALUES ({placeholders})"
    with write_lock:
        init_db()
        cursor = connect().execute(sql, tuple(row[name] for name in allowed))
        connect().commit()
        return int(cursor.lastrowid or 0)


def upsert_store(row: dict) -> int:
    payload = {name: row.get(name) for name in COLUMNS["stores"] if name != "id"}
    with write_lock:
        init_db()
        connect().execute(
            """
            INSERT INTO stores (name, chain, address_norm, zip, lat, lng, hours, source_url, found_ts, phone)
            VALUES (:name, :chain, :address_norm, :zip, :lat, :lng, :hours, :source_url, :found_ts, :phone)
            ON CONFLICT(chain, address_norm) DO UPDATE SET
              name=excluded.name,
              zip=excluded.zip,
              lat=excluded.lat,
              lng=excluded.lng,
              hours=excluded.hours,
              source_url=excluded.source_url,
              found_ts=excluded.found_ts,
              phone=COALESCE(excluded.phone, stores.phone)
            """,
            payload,
        )
        connect().commit()
        found = connect().execute(
            "SELECT id FROM stores WHERE chain = ? AND address_norm = ?",
            (payload["chain"], payload["address_norm"]),
        ).fetchone()
        return int(found["id"])


def get_kv(key: str) -> str | None:
    with write_lock:
        init_db()
        row = connect().execute("SELECT value FROM kv WHERE key = ?", (key,)).fetchone()
        return None if row is None else str(row["value"])


def set_kv(key: str, value: str) -> None:
    with write_lock:
        init_db()
        connect().execute(
            "INSERT INTO kv (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        connect().commit()


def forecast_run_utc() -> str:
    """Start of the newest forecast run. Older forecast rows are history, not current risk:
    an item that recovers gets no new row, so reading MAX(id) alone kept stale risks on screen."""
    return get_kv("forecast_run_utc") or ""
