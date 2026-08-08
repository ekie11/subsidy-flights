"""
Хранилище: SQLite. Одна таблица наблюдений (append-only) + таблица алертов.

Такая схема даёт и текущее состояние (последнее наблюдение по ключу рейса),
и историю — из неё потом строятся графики «как таяли места».
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator

import config
from parser import FlightOffer


SCHEMA = """
CREATE TABLE IF NOT EXISTS observations (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    flight_key    TEXT NOT NULL,
    observed_at   TEXT NOT NULL,
    route         TEXT NOT NULL,
    origin        TEXT,
    destination   TEXT,
    depart_date   TEXT NOT NULL,
    depart_time   TEXT,
    arrive_time   TEXT,
    flight_number TEXT,
    airline       TEXT,
    fare_code     TEXT,
    mrid          TEXT,
    avail_qty     INTEGER NOT NULL DEFAULT 0,
    price         REAL NOT NULL DEFAULT 0,
    currency      TEXT,
    book_url      TEXT
);
CREATE INDEX IF NOT EXISTS idx_obs_key_time  ON observations(flight_key, observed_at DESC);
CREATE INDEX IF NOT EXISTS idx_obs_route     ON observations(route, depart_date);
CREATE INDEX IF NOT EXISTS idx_obs_time      ON observations(observed_at DESC);

CREATE TABLE IF NOT EXISTS alerts (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at   TEXT NOT NULL,
    flight_key   TEXT NOT NULL,
    alert_type   TEXT NOT NULL,
    severity     TEXT NOT NULL DEFAULT 'info',
    message      TEXT NOT NULL,
    prev_qty     INTEGER,
    new_qty      INTEGER,
    delivered    INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_alerts_key  ON alerts(flight_key, alert_type, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_alerts_time ON alerts(created_at DESC);

CREATE TABLE IF NOT EXISTS runs (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at   TEXT NOT NULL,
    finished_at  TEXT,
    requests     INTEGER DEFAULT 0,
    offers       INTEGER DEFAULT 0,
    errors       INTEGER DEFAULT 0,
    dry_run      INTEGER DEFAULT 0,
    note         TEXT
);
"""


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Database:
    def __init__(self, path: Path | str | None = None):
        self.path = Path(path or config.DB_PATH)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _init_schema(self) -> None:
        with self.connect() as conn:
            conn.executescript(SCHEMA)

    # ------------------------------------------------------------ наблюдения

    def latest_by_key(self, keys: Iterable[str] | None = None) -> dict[str, sqlite3.Row]:
        """Последнее наблюдение по каждому ключу рейса."""
        sql = """
            SELECT o.* FROM observations o
            JOIN (
                SELECT flight_key, MAX(id) AS max_id
                FROM observations GROUP BY flight_key
            ) last ON last.max_id = o.id
        """
        params: list[Any] = []
        if keys is not None:
            keys = list(keys)
            if not keys:
                return {}  # пустой фильтр — это «ничего», а не «всё»
            sql += f" WHERE o.flight_key IN ({','.join('?' * len(keys))})"
            params = keys
        with self.connect() as conn:
            return {r["flight_key"]: r for r in conn.execute(sql, params)}

    def save_observations(self, offers: Iterable[FlightOffer],
                          observed_at: str | None = None) -> int:
        ts = observed_at or utcnow()
        rows = [(
            o.key(), ts, o.route, o.origin, o.destination, o.depart_date,
            o.depart_time, o.arrive_time, o.flight_number, o.airline,
            o.fare_code, o.mrid, o.avail_qty, o.price, o.currency, o.book_url,
        ) for o in offers]
        if not rows:
            return 0
        with self.connect() as conn:
            conn.executemany("""
                INSERT INTO observations (
                    flight_key, observed_at, route, origin, destination,
                    depart_date, depart_time, arrive_time, flight_number,
                    airline, fare_code, mrid, avail_qty, price, currency, book_url
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, rows)
        return len(rows)

    def history(self, flight_key: str, limit: int = 200) -> list[sqlite3.Row]:
        with self.connect() as conn:
            return list(conn.execute("""
                SELECT observed_at, avail_qty, price FROM observations
                WHERE flight_key = ? ORDER BY id DESC LIMIT ?
            """, (flight_key, limit)))

    def current_state(self) -> list[sqlite3.Row]:
        """Актуальный срез: последнее наблюдение по каждому рейсу, будущие даты."""
        today = datetime.now(timezone.utc).date().isoformat()
        with self.connect() as conn:
            return list(conn.execute("""
                SELECT o.* FROM observations o
                JOIN (
                    SELECT flight_key, MAX(id) AS max_id
                    FROM observations GROUP BY flight_key
                ) last ON last.max_id = o.id
                WHERE o.depart_date >= ?
                ORDER BY o.route, o.depart_date, o.depart_time
            """, (today,)))

    # ---------------------------------------------------------------- алерты

    def last_alert_at(self, flight_key: str, alert_type: str) -> str | None:
        with self.connect() as conn:
            row = conn.execute("""
                SELECT created_at FROM alerts
                WHERE flight_key = ? AND alert_type = ?
                ORDER BY id DESC LIMIT 1
            """, (flight_key, alert_type)).fetchone()
        return row["created_at"] if row else None

    def save_alert(self, flight_key: str, alert_type: str, severity: str,
                   message: str, prev_qty: int | None, new_qty: int | None,
                   delivered: bool = False) -> int:
        with self.connect() as conn:
            cur = conn.execute("""
                INSERT INTO alerts (created_at, flight_key, alert_type, severity,
                                    message, prev_qty, new_qty, delivered)
                VALUES (?,?,?,?,?,?,?,?)
            """, (utcnow(), flight_key, alert_type, severity, message,
                  prev_qty, new_qty, int(delivered)))
            return int(cur.lastrowid)

    def recent_alerts(self, limit: int = 50) -> list[sqlite3.Row]:
        with self.connect() as conn:
            return list(conn.execute("""
                SELECT * FROM alerts ORDER BY id DESC LIMIT ?
            """, (limit,)))

    # ------------------------------------------------------------------ runs

    def start_run(self, dry_run: bool) -> int:
        with self.connect() as conn:
            cur = conn.execute(
                "INSERT INTO runs (started_at, dry_run) VALUES (?,?)",
                (utcnow(), int(dry_run)))
            return int(cur.lastrowid)

    def finish_run(self, run_id: int, requests: int, offers: int,
                   errors: int, note: str = "") -> None:
        with self.connect() as conn:
            conn.execute("""
                UPDATE runs SET finished_at=?, requests=?, offers=?, errors=?, note=?
                WHERE id=?
            """, (utcnow(), requests, offers, errors, note, run_id))

    def last_runs(self, limit: int = 10) -> list[sqlite3.Row]:
        with self.connect() as conn:
            return list(conn.execute(
                "SELECT * FROM runs ORDER BY id DESC LIMIT ?", (limit,)))
