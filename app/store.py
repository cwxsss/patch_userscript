# -*- coding: utf-8 -*-
"""SQLite 落库层。

设计目标：零外部依赖、开箱即用。所有写入都是 best-effort——
数据库出问题绝不能让识别接口失败，异常一律吞掉并打日志。
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

from . import settings

_LOCK = threading.RLock()
_CONN: sqlite3.Connection | None = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS recognition_log (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    ts            TEXT    NOT NULL,
    ip            TEXT,
    kind          TEXT,              -- normal | slide
    host          TEXT,
    href          TEXT,
    route_path    TEXT,
    device_id     TEXT,
    visit_id      TEXT,
    request_id    TEXT,
    image_sha256  TEXT,
    result        TEXT,
    confidence    REAL,
    raw_distance  REAL,
    distance      REAL,
    ok            INTEGER,
    error_code    TEXT,
    duration_ms   INTEGER
);
CREATE INDEX IF NOT EXISTS idx_recog_ts   ON recognition_log(ts);
CREATE INDEX IF NOT EXISTS idx_recog_host ON recognition_log(host);

CREATE TABLE IF NOT EXISTS feedback (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ts              TEXT,
    ip              TEXT,
    host            TEXT,
    request_id      TEXT,
    recognized      TEXT,
    corrected       TEXT,
    accepted        INTEGER,
    payload_json    TEXT
);

CREATE TABLE IF NOT EXISTS slide_sample (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    ts            TEXT,
    ip            TEXT,
    subtype       TEXT,              -- trajectory | human | solve
    host          TEXT,
    href          TEXT,
    outcome       TEXT,
    distance      REAL,
    payload_json  TEXT
);
CREATE INDEX IF NOT EXISTS idx_slide_sub ON slide_sample(subtype);

CREATE TABLE IF NOT EXISTS event (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    ts            TEXT,
    ip            TEXT,
    host          TEXT,
    event_name    TEXT,
    payload_json  TEXT
);
CREATE INDEX IF NOT EXISTS idx_event_ts ON event(ts);

CREATE TABLE IF NOT EXISTS binding (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    ts            TEXT,
    ip            TEXT,
    kind          TEXT,              -- normal | slide
    subtype       TEXT,              -- report | event
    host          TEXT,
    route_path    TEXT,
    device_id     TEXT,
    payload_json  TEXT
);

CREATE TABLE IF NOT EXISTS identity (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    ts            TEXT,
    ip            TEXT,
    device_id     TEXT,
    payload_json  TEXT
);

CREATE TABLE IF NOT EXISTS quota_usage (
    ip     TEXT NOT NULL,
    day    TEXT NOT NULL,
    kind   TEXT NOT NULL DEFAULT 'recognition',
    count  INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (ip, day, kind)
);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""


def _connect() -> sqlite3.Connection | None:
    global _CONN
    if not settings.get()["storage"].get("enabled", True):
        return None
    with _LOCK:
        if _CONN is not None:
            return _CONN
        path: Path = settings.db_path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(str(path), check_same_thread=False, timeout=10)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.executescript(SCHEMA)
            conn.commit()
            _CONN = conn
            return _CONN
        except Exception as exc:
            print(f"[store] 初始化 SQLite 失败，落库功能已禁用: {exc}")
            return None


def init() -> None:
    _connect()


def reset_connection() -> None:
    global _CONN
    with _LOCK:
        _CONN = None


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())


def sha256_hex(data: bytes | None) -> str:
    if not data:
        return ""
    return hashlib.sha256(data).hexdigest()


def _exec(sql: str, params: tuple) -> None:
    conn = _connect()
    if conn is None:
        return
    try:
        with _LOCK:
            conn.execute(sql, params)
            conn.commit()
    except Exception as exc:
        print(f"[store] 写入失败（已忽略）: {exc}")


def _json(value: Any, limit: int = 200_000) -> str:
    try:
        text = json.dumps(value, ensure_ascii=False, default=str)
    except Exception:
        text = json.dumps({"_unserializable": True})
    return text[:limit]


def log_recognition(**kw) -> None:
    _exec(
        """INSERT INTO recognition_log
           (ts, ip, kind, host, href, route_path, device_id, visit_id, request_id,
            image_sha256, result, confidence, raw_distance, distance, ok, error_code, duration_ms)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            now_iso(),
            kw.get("ip", ""),
            kw.get("kind", ""),
            kw.get("host", ""),
            (kw.get("href", "") or "")[:2000],
            kw.get("route_path", ""),
            kw.get("device_id", ""),
            kw.get("visit_id", ""),
            kw.get("request_id", ""),
            kw.get("image_sha256", ""),
            (kw.get("result", "") or "")[:500],
            kw.get("confidence"),
            kw.get("raw_distance"),
            kw.get("distance"),
            1 if kw.get("ok") else 0,
            kw.get("error_code", ""),
            int(kw.get("duration_ms") or 0),
        ),
    )


def log_feedback(ip: str, host: str, payload: dict) -> None:
    _exec(
        """INSERT INTO feedback (ts, ip, host, request_id, recognized, corrected, accepted, payload_json)
           VALUES (?,?,?,?,?,?,?,?)""",
        (
            now_iso(),
            ip,
            host,
            str(payload.get("requestId") or payload.get("request_id") or ""),
            str(payload.get("recognizedValue") or payload.get("recognized_value") or
                payload.get("value") or payload.get("originalValue") or "")[:200],
            str(payload.get("correctedValue") or payload.get("corrected_value") or
                payload.get("filledValue") or payload.get("corrected") or "")[:200],
            1 if payload.get("correctedSubmitted") or payload.get("accepted") else 0,
            _json(payload),
        ),
    )


def log_slide_sample(ip: str, subtype: str, payload: dict) -> None:
    if not settings.get()["storage"].get("store_samples", True):
        return
    meta = payload.get("meta") if isinstance(payload.get("meta"), dict) else {}
    distance = payload.get("distance", payload.get("actualDistance"))
    if distance is None and isinstance(payload.get("trajectory"), dict):
        distance = payload["trajectory"].get("distance")
    _exec(
        """INSERT INTO slide_sample (ts, ip, subtype, host, href, outcome, distance, payload_json)
           VALUES (?,?,?,?,?,?,?,?)""",
        (
            now_iso(),
            ip,
            subtype,
            str(meta.get("host") or payload.get("host") or ""),
            str(meta.get("href") or "")[:2000],
            str(payload.get("outcome") or payload.get("result") or "")[:80],
            float(distance) if isinstance(distance, (int, float)) else None,
            _json(payload),
        ),
    )


def log_event(ip: str, host: str, event_name: str, payload: dict) -> None:
    if not settings.get()["storage"].get("store_events", True):
        return
    _exec(
        "INSERT INTO event (ts, ip, host, event_name, payload_json) VALUES (?,?,?,?,?)",
        (now_iso(), ip, host, str(event_name or "")[:120], _json(payload, 60_000)),
    )


def log_binding(ip: str, kind: str, subtype: str, payload: dict) -> None:
    meta = payload.get("meta") if isinstance(payload.get("meta"), dict) else {}
    _exec(
        """INSERT INTO binding (ts, ip, kind, subtype, host, route_path, device_id, payload_json)
           VALUES (?,?,?,?,?,?,?,?)""",
        (
            now_iso(),
            ip,
            kind,
            subtype,
            str(meta.get("host") or payload.get("host") or ""),
            str(meta.get("pathname") or payload.get("pathname") or ""),
            str(meta.get("deviceId") or payload.get("deviceId") or payload.get("device_id") or ""),
            _json(payload, 60_000),
        ),
    )


def log_identity(ip: str, payload: dict) -> None:
    _exec(
        "INSERT INTO identity (ts, ip, device_id, payload_json) VALUES (?,?,?,?)",
        (
            now_iso(),
            ip,
            str(payload.get("deviceId") or payload.get("device_id") or "")[:120],
            _json(payload, 60_000),
        ),
    )


# ---------------------------------------------------------------------------
# 每日配额
# ---------------------------------------------------------------------------
def today() -> str:
    return time.strftime("%Y-%m-%d", time.localtime())


def quota_peek(ip: str, kind: str = "recognition") -> int:
    conn = _connect()
    if conn is None:
        return 0
    try:
        with _LOCK:
            row = conn.execute(
                "SELECT count FROM quota_usage WHERE ip=? AND day=? AND kind=?",
                (ip, today(), kind),
            ).fetchone()
        return int(row["count"]) if row else 0
    except Exception:
        return 0


def quota_consume(ip: str, kind: str = "recognition") -> int:
    """计数 +1 并返回当日累计值。"""
    conn = _connect()
    if conn is None:
        return 0
    day = today()
    try:
        with _LOCK:
            conn.execute(
                """INSERT INTO quota_usage (ip, day, kind, count) VALUES (?,?,?,1)
                   ON CONFLICT(ip, day, kind) DO UPDATE SET count = count + 1""",
                (ip, day, kind),
            )
            conn.commit()
            row = conn.execute(
                "SELECT count FROM quota_usage WHERE ip=? AND day=? AND kind=?",
                (ip, day, kind),
            ).fetchone()
        return int(row["count"]) if row else 1
    except Exception:
        return 0


def stats() -> dict:
    conn = _connect()
    if conn is None:
        return {"enabled": False}
    out: dict = {"enabled": True, "db": str(settings.db_path())}
    try:
        with _LOCK:
            for table in ("recognition_log", "feedback", "slide_sample", "event", "binding", "identity"):
                row = conn.execute(f"SELECT COUNT(*) AS c FROM {table}").fetchone()
                out[table] = int(row["c"]) if row else 0
            row = conn.execute(
                "SELECT COUNT(*) AS c, SUM(ok) AS ok FROM recognition_log WHERE kind='normal'"
            ).fetchone()
            total = int(row["c"]) if row else 0
            good = int(row["ok"] or 0)
            out["normal_total"] = total
            out["normal_ok"] = good
            out["normal_ok_rate"] = round(good / total, 4) if total else 0.0
    except Exception as exc:
        out["error"] = str(exc)
    return out
