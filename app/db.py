import json
import sqlite3
from collections.abc import Generator, Iterable
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any

import pandas as pd

from app.config import DB_PATH


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def get_connection() -> Generator[sqlite3.Connection, None, None]:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with get_connection() as conn:
        conn.executescript(
            """
            PRAGMA foreign_keys = ON;

            CREATE TABLE IF NOT EXISTS test_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                source_file TEXT NOT NULL,
                started_at TEXT,
                ended_at TEXT,
                total_rows INTEGER NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS samples (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id INTEGER NOT NULL,
                ts TEXT NOT NULL,
                speed_mps REAL,
                steering_deg REAL,
                acceleration_mps2 REAL,
                lane_id TEXT,
                distance_to_lead_m REAL,
                stop_sign_detected INTEGER DEFAULT 0,
                sensors_json TEXT,
                FOREIGN KEY(run_id) REFERENCES test_runs(id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_samples_run_id ON samples(run_id);
            CREATE INDEX IF NOT EXISTS idx_samples_run_ts ON samples(run_id, ts);

            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id INTEGER NOT NULL,
                event_type TEXT NOT NULL,
                ts TEXT NOT NULL,
                severity TEXT NOT NULL,
                details_json TEXT,
                FOREIGN KEY(run_id) REFERENCES test_runs(id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_events_run_id ON events(run_id);

            CREATE TABLE IF NOT EXISTS failures (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id INTEGER NOT NULL,
                event_id INTEGER,
                reason TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'open',
                notes TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY(run_id) REFERENCES test_runs(id) ON DELETE CASCADE,
                FOREIGN KEY(event_id) REFERENCES events(id) ON DELETE SET NULL
            );

            CREATE INDEX IF NOT EXISTS idx_failures_run_id ON failures(run_id);

            CREATE TABLE IF NOT EXISTS run_policy_assessments (
                run_id INTEGER PRIMARY KEY,
                policy_json TEXT NOT NULL,
                verdict TEXT NOT NULL,
                summary_json TEXT NOT NULL,
                evaluated_at TEXT NOT NULL,
                FOREIGN KEY(run_id) REFERENCES test_runs(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS run_policy_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id INTEGER NOT NULL,
                version INTEGER NOT NULL,
                policy_json TEXT NOT NULL,
                verdict TEXT NOT NULL,
                summary_json TEXT NOT NULL,
                evaluated_at TEXT NOT NULL,
                UNIQUE(run_id, version),
                FOREIGN KEY(run_id) REFERENCES test_runs(id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_policy_snapshots_run_id_version
            ON run_policy_snapshots(run_id, version DESC);

            CREATE TABLE IF NOT EXISTS reviewer_audit_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                action TEXT NOT NULL,
                resource_type TEXT NOT NULL,
                resource_id INTEGER,
                outcome TEXT NOT NULL,
                reason TEXT,
                reviewer_key_provided INTEGER NOT NULL DEFAULT 0,
                actor_ip TEXT,
                details_json TEXT,
                created_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_reviewer_audit_events_created_at
            ON reviewer_audit_events(created_at DESC);

            CREATE TABLE IF NOT EXISTS reviewer_auth_settings (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                active_key TEXT NOT NULL,
                previous_key TEXT,
                previous_key_expires_at TEXT,
                invalid_limit INTEGER NOT NULL,
                invalid_window_seconds INTEGER NOT NULL,
                lockout_seconds INTEGER NOT NULL,
                updated_at TEXT NOT NULL
            );
            """
        )


def create_test_run(name: str, source_file: str, samples_df: pd.DataFrame) -> int:
    started_at = samples_df["ts"].min() if not samples_df.empty else None
    ended_at = samples_df["ts"].max() if not samples_df.empty else None
    started_at_str = to_iso_or_none(started_at)
    ended_at_str = to_iso_or_none(ended_at)

    with get_connection() as conn:
        cursor = conn.execute(
            """
            INSERT INTO test_runs (name, source_file, started_at, ended_at, total_rows, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (name, source_file, started_at_str, ended_at_str, len(samples_df), utc_now_iso()),
        )
        run_id = cursor.lastrowid
        if run_id is None:
            raise RuntimeError("Failed to create test run row")

        sample_rows: list[tuple[Any, ...]] = []
        for _, row in samples_df.iterrows():
            sensors = {
                "raw": row.get("raw", {}),
                "source": row.get("source", "log_ingest"),
            }
            sample_rows.append(
                (
                    run_id,
                    str(row["ts"]),
                    nullable_float(row.get("speed_mps")),
                    nullable_float(row.get("steering_deg")),
                    nullable_float(row.get("acceleration_mps2")),
                    nullable_text(row.get("lane_id")),
                    nullable_float(row.get("distance_to_lead_m")),
                    int(bool(row.get("stop_sign_detected", False))),
                    json.dumps(sensors),
                )
            )

        conn.executemany(
            """
            INSERT INTO samples
            (run_id, ts, speed_mps, steering_deg, acceleration_mps2, lane_id, distance_to_lead_m, stop_sign_detected, sensors_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            sample_rows,
        )

    return int(run_id)


def insert_events(run_id: int, events: Iterable[dict[str, Any]]) -> None:
    rows = [
        (
            run_id,
            e["event_type"],
            str(e["ts"]),
            e["severity"],
            json.dumps(e.get("details", {})),
        )
        for e in events
    ]

    if not rows:
        return

    with get_connection() as conn:
        conn.executemany(
            """
            INSERT INTO events (run_id, event_type, ts, severity, details_json)
            VALUES (?, ?, ?, ?, ?)
            """,
            rows,
        )


def create_failure(run_id: int, reason: str, event_id: int | None = None, notes: str | None = None) -> None:
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO failures (run_id, event_id, reason, status, notes, created_at)
            VALUES (?, ?, ?, 'open', ?, ?)
            """,
            (run_id, event_id, reason, notes, utc_now_iso()),
        )


def update_failure_status(failure_id: int, status: str, notes: str | None = None) -> None:
    with get_connection() as conn:
        conn.execute(
            """
            UPDATE failures
            SET status = ?, notes = COALESCE(?, notes)
            WHERE id = ?
            """,
            (status, notes, failure_id),
        )


def get_runs() -> pd.DataFrame:
    with get_connection() as conn:
        return pd.read_sql_query(
            """
            SELECT id, name, source_file, started_at, ended_at, total_rows, created_at
            FROM test_runs
            ORDER BY id DESC
            """,
            conn,
        )


def get_run_samples(run_id: int) -> pd.DataFrame:
    with get_connection() as conn:
        return pd.read_sql_query(
            """
            SELECT id, ts, speed_mps, steering_deg, acceleration_mps2, lane_id,
                   distance_to_lead_m, stop_sign_detected
            FROM samples
            WHERE run_id = ?
            ORDER BY ts
            """,
            conn,
            params=[run_id],
        )


def get_run_events(run_id: int) -> pd.DataFrame:
    with get_connection() as conn:
        return pd.read_sql_query(
            """
            SELECT id, run_id, event_type, ts, severity, details_json
            FROM events
            WHERE run_id = ?
            ORDER BY ts
            """,
            conn,
            params=[run_id],
        )


def get_run_failures(run_id: int) -> pd.DataFrame:
    with get_connection() as conn:
        return pd.read_sql_query(
            """
            SELECT id, run_id, event_id, reason, status, notes, created_at
            FROM failures
            WHERE run_id = ?
            ORDER BY created_at DESC
            """,
            conn,
            params=[run_id],
        )


def delete_run(run_id: int) -> bool:
    with get_connection() as conn:
        cursor = conn.execute("DELETE FROM test_runs WHERE id = ?", (run_id,))
        return cursor.rowcount > 0


def upsert_run_policy_assessment(
    run_id: int,
    policy_json: str,
    verdict: str,
    summary_json: str,
) -> None:
    with get_connection() as conn:
        next_version = conn.execute(
            """
            SELECT COALESCE(MAX(version), 0) + 1
            FROM run_policy_snapshots
            WHERE run_id = ?
            """,
            (run_id,),
        ).fetchone()[0]
        evaluated_at = utc_now_iso()

        conn.execute(
            """
            INSERT INTO run_policy_snapshots
            (run_id, version, policy_json, verdict, summary_json, evaluated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (run_id, int(next_version), policy_json, verdict, summary_json, evaluated_at),
        )

        conn.execute(
            """
            INSERT INTO run_policy_assessments (run_id, policy_json, verdict, summary_json, evaluated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(run_id) DO UPDATE SET
                policy_json = excluded.policy_json,
                verdict = excluded.verdict,
                summary_json = excluded.summary_json,
                evaluated_at = excluded.evaluated_at
            """,
            (run_id, policy_json, verdict, summary_json, evaluated_at),
        )


def get_run_policy_assessment(run_id: int) -> dict[str, Any] | None:
    with get_connection() as conn:
        snapshot_row = conn.execute(
            """
            SELECT id, run_id, version, policy_json, verdict, summary_json, evaluated_at
            FROM run_policy_snapshots
            WHERE run_id = ?
            ORDER BY version DESC
            LIMIT 1
            """,
            (run_id,),
        ).fetchone()

    if snapshot_row is not None:
        return {
            "snapshot_id": int(snapshot_row["id"]),
            "run_id": int(snapshot_row["run_id"]),
            "version": int(snapshot_row["version"]),
            "policy_json": str(snapshot_row["policy_json"]),
            "verdict": str(snapshot_row["verdict"]),
            "summary_json": str(snapshot_row["summary_json"]),
            "evaluated_at": str(snapshot_row["evaluated_at"]),
        }

    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT run_id, policy_json, verdict, summary_json, evaluated_at
            FROM run_policy_assessments
            WHERE run_id = ?
            """,
            (run_id,),
        ).fetchone()

    if row is None:
        return None

    return {
        "snapshot_id": None,
        "run_id": int(row["run_id"]),
        "version": 1,
        "policy_json": str(row["policy_json"]),
        "verdict": str(row["verdict"]),
        "summary_json": str(row["summary_json"]),
        "evaluated_at": str(row["evaluated_at"]),
    }


def list_run_policy_snapshots(run_id: int) -> list[dict[str, Any]]:
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT id, run_id, version, policy_json, verdict, summary_json, evaluated_at
            FROM run_policy_snapshots
            WHERE run_id = ?
            ORDER BY version DESC
            """,
            (run_id,),
        ).fetchall()

    return [
        {
            "snapshot_id": int(row["id"]),
            "run_id": int(row["run_id"]),
            "version": int(row["version"]),
            "policy_json": str(row["policy_json"]),
            "verdict": str(row["verdict"]),
            "summary_json": str(row["summary_json"]),
            "evaluated_at": str(row["evaluated_at"]),
        }
        for row in rows
    ]


def create_reviewer_audit_event(
    action: str,
    outcome: str,
    resource_type: str = "run",
    resource_id: int | None = None,
    reason: str | None = None,
    reviewer_key_provided: bool = False,
    actor_ip: str | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO reviewer_audit_events
            (action, resource_type, resource_id, outcome, reason, reviewer_key_provided, actor_ip, details_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                action,
                resource_type,
                resource_id,
                outcome,
                reason,
                int(reviewer_key_provided),
                actor_ip,
                json.dumps(details or {}),
                utc_now_iso(),
            ),
        )


def list_reviewer_audit_events(limit: int = 100) -> list[dict[str, Any]]:
    safe_limit = max(1, min(limit, 500))
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT id, action, resource_type, resource_id, outcome, reason,
                   reviewer_key_provided, actor_ip, details_json, created_at
            FROM reviewer_audit_events
            ORDER BY id DESC
            LIMIT ?
            """,
            (safe_limit,),
        ).fetchall()

    return [
        {
            "id": int(row["id"]),
            "action": str(row["action"]),
            "resource_type": str(row["resource_type"]),
            "resource_id": int(row["resource_id"]) if row["resource_id"] is not None else None,
            "outcome": str(row["outcome"]),
            "reason": str(row["reason"]) if row["reason"] is not None else None,
            "reviewer_key_provided": bool(row["reviewer_key_provided"]),
            "actor_ip": str(row["actor_ip"]) if row["actor_ip"] is not None else None,
            "details": json.loads(str(row["details_json"]) or "{}"),
            "created_at": str(row["created_at"]),
        }
        for row in rows
    ]


def get_reviewer_auth_settings() -> dict[str, Any] | None:
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT id, active_key, previous_key, previous_key_expires_at,
                   invalid_limit, invalid_window_seconds, lockout_seconds, updated_at
            FROM reviewer_auth_settings
            WHERE id = 1
            """
        ).fetchone()

    if row is None:
        return None

    return {
        "id": int(row["id"]),
        "active_key": str(row["active_key"]),
        "previous_key": str(row["previous_key"]) if row["previous_key"] is not None else None,
        "previous_key_expires_at": str(row["previous_key_expires_at"]) if row["previous_key_expires_at"] is not None else None,
        "invalid_limit": int(row["invalid_limit"]),
        "invalid_window_seconds": int(row["invalid_window_seconds"]),
        "lockout_seconds": int(row["lockout_seconds"]),
        "updated_at": str(row["updated_at"]),
    }


def upsert_reviewer_auth_settings(
    active_key: str,
    previous_key: str | None,
    previous_key_expires_at: str | None,
    invalid_limit: int,
    invalid_window_seconds: int,
    lockout_seconds: int,
) -> None:
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO reviewer_auth_settings
            (id, active_key, previous_key, previous_key_expires_at, invalid_limit, invalid_window_seconds, lockout_seconds, updated_at)
            VALUES (1, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                active_key = excluded.active_key,
                previous_key = excluded.previous_key,
                previous_key_expires_at = excluded.previous_key_expires_at,
                invalid_limit = excluded.invalid_limit,
                invalid_window_seconds = excluded.invalid_window_seconds,
                lockout_seconds = excluded.lockout_seconds,
                updated_at = excluded.updated_at
            """,
            (
                active_key,
                previous_key,
                previous_key_expires_at,
                invalid_limit,
                invalid_window_seconds,
                lockout_seconds,
                utc_now_iso(),
            ),
        )


def clear_reviewer_auth_settings() -> None:
    with get_connection() as conn:
        conn.execute("DELETE FROM reviewer_auth_settings WHERE id = 1")


def nullable_float(value: Any) -> float | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def nullable_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text if text else None


def to_iso_or_none(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    text = str(value).strip()
    return text if text else None
