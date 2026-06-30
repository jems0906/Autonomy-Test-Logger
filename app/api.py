from __future__ import annotations

import json
import os
from collections import defaultdict, deque
from datetime import datetime, timezone
from typing import Any

import pandas as pd
from fastapi import FastAPI, Header, HTTPException, Request
from pydantic import BaseModel, Field

from app.db import (
    create_failure,
    create_reviewer_audit_event,
    create_test_run,
    delete_run,
    get_reviewer_auth_settings,
    get_run_events,
    get_run_failures,
    get_run_policy_assessment,
    get_run_samples,
    get_runs,
    init_db,
    insert_events,
    list_reviewer_audit_events,
    list_run_policy_snapshots,
    upsert_reviewer_auth_settings,
    upsert_run_policy_assessment,
)
from app.detection import DetectionThresholds, detect_events
from app.ingestion import normalize_log_dataframe
from app.policy import PolicyConfig, build_policy_failures, evaluate_policy
from app.reporting import build_run_summary

app = FastAPI(title="Autonomy Test Logger API", version="1.0.0")
init_db()


INVALID_REVIEWER_ATTEMPTS: dict[str, deque[datetime]] = defaultdict(deque)


class ThresholdPayload(BaseModel):
    harsh_brake_mps2: float = -2.8
    hard_accel_mps2: float = 2.8
    aggressive_steering_deg: float = 15.0
    speeding_mps: float = 27.0
    cut_in_drop_m: float = 8.0


class PolicyPayload(BaseModel):
    max_cut_in_events: int = 0
    max_speeding_events: int = 0
    max_stop_sign_violations: int = 0
    max_braking_events: int = 2
    max_aggressive_steering_events: int = 3
    max_hard_accel_events: int = 3


class IngestPayload(BaseModel):
    run_name: str = Field(min_length=1, max_length=200)
    source_file: str = Field(default="api_ingest")
    records: list[dict[str, Any]] = Field(default_factory=list)
    thresholds: ThresholdPayload = Field(default_factory=ThresholdPayload)
    policy: PolicyPayload = Field(default_factory=PolicyPayload)


class ReviewerAuthUpdatePayload(BaseModel):
    active_key: str | None = Field(default=None, min_length=1, max_length=200)
    previous_key: str | None = Field(default=None, max_length=200)
    previous_key_expires_at: datetime | None = None
    invalid_limit: int | None = Field(default=None, ge=1, le=1000)
    invalid_window_seconds: int | None = Field(default=None, ge=1, le=86400)
    lockout_seconds: int | None = Field(default=None, ge=1, le=86400)


def get_reviewer_key() -> str:
    settings = get_reviewer_auth_settings()
    if settings is not None:
        return str(settings["active_key"])
    return os.getenv("ATL_REVIEWER_KEY", "reviewer")


def get_reviewer_previous_key() -> str | None:
    settings = get_reviewer_auth_settings()
    if settings is not None:
        return settings["previous_key"]
    value = os.getenv("ATL_REVIEWER_PREVIOUS_KEY", "").strip()
    return value if value else None


def parse_utc_iso_datetime(value: str) -> datetime | None:
    if not value:
        return None
    normalized = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def get_reviewer_previous_key_expires_at() -> datetime | None:
    settings = get_reviewer_auth_settings()
    if settings is not None:
        return parse_utc_iso_datetime(settings["previous_key_expires_at"] or "")
    value = os.getenv("ATL_REVIEWER_PREVIOUS_KEY_EXPIRES_AT", "")
    return parse_utc_iso_datetime(value)


def classify_reviewer_key(provided_key: str | None, now: datetime) -> str | None:
    if not provided_key:
        return None

    if provided_key == get_reviewer_key():
        return "active"

    previous_key = get_reviewer_previous_key()
    if previous_key is None or provided_key != previous_key:
        return None

    expires_at = get_reviewer_previous_key_expires_at()
    if expires_at is None or now >= expires_at:
        return None

    return "previous"


def get_reviewer_invalid_limit() -> int:
    settings = get_reviewer_auth_settings()
    if settings is not None:
        return int(settings["invalid_limit"])

    raw = os.getenv("ATL_REVIEWER_INVALID_LIMIT", "20")
    try:
        value = int(raw)
    except ValueError:
        value = 20
    return max(1, min(value, 1000))


def get_reviewer_invalid_window_seconds() -> int:
    settings = get_reviewer_auth_settings()
    if settings is not None:
        return int(settings["invalid_window_seconds"])

    raw = os.getenv("ATL_REVIEWER_INVALID_WINDOW_SECONDS", "300")
    try:
        value = int(raw)
    except ValueError:
        value = 300
    return max(1, min(value, 86400))


def get_reviewer_lockout_seconds() -> int:
    settings = get_reviewer_auth_settings()
    if settings is not None:
        return int(settings["lockout_seconds"])

    raw = os.getenv("ATL_REVIEWER_LOCKOUT_SECONDS", "120")
    try:
        value = int(raw)
    except ValueError:
        value = 120
    return max(1, min(value, 86400))


def _prune_invalid_attempts(actor_key: str, now: datetime) -> deque[datetime]:
    attempts = INVALID_REVIEWER_ATTEMPTS[actor_key]
    window_seconds = get_reviewer_invalid_window_seconds()
    while attempts and (now - attempts[0]).total_seconds() > window_seconds:
        attempts.popleft()
    return attempts


def _is_reviewer_locked(actor_key: str, now: datetime) -> tuple[bool, int]:
    attempts = _prune_invalid_attempts(actor_key, now)
    invalid_limit = get_reviewer_invalid_limit()
    lockout_seconds = get_reviewer_lockout_seconds()

    if len(attempts) < invalid_limit:
        return False, 0

    elapsed = (now - attempts[-1]).total_seconds()
    if elapsed >= lockout_seconds:
        return False, 0

    retry_after = max(1, int(lockout_seconds - elapsed))
    return True, retry_after


def _register_invalid_attempt(actor_key: str, now: datetime) -> None:
    attempts = _prune_invalid_attempts(actor_key, now)
    attempts.append(now)


def _clear_invalid_attempts(actor_key: str) -> None:
    INVALID_REVIEWER_ATTEMPTS.pop(actor_key, None)


def reset_reviewer_guard_state() -> None:
    INVALID_REVIEWER_ATTEMPTS.clear()


def get_current_reviewer_auth_snapshot() -> dict[str, Any]:
    settings = get_reviewer_auth_settings()
    if settings is None:
        previous_key = get_reviewer_previous_key()
        previous_key_expires_at = get_reviewer_previous_key_expires_at()
        return {
            "active_key": get_reviewer_key(),
            "previous_key": previous_key,
            "previous_key_expires_at": previous_key_expires_at.isoformat() if previous_key_expires_at is not None else None,
            "invalid_limit": get_reviewer_invalid_limit(),
            "invalid_window_seconds": get_reviewer_invalid_window_seconds(),
            "lockout_seconds": get_reviewer_lockout_seconds(),
        }

    return {
        "active_key": str(settings["active_key"]),
        "previous_key": settings["previous_key"],
        "previous_key_expires_at": settings["previous_key_expires_at"],
        "invalid_limit": int(settings["invalid_limit"]),
        "invalid_window_seconds": int(settings["invalid_window_seconds"]),
        "lockout_seconds": int(settings["lockout_seconds"]),
    }


def _redact_reviewer_auth_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    return {
        "active_key_set": bool(snapshot["active_key"]),
        "previous_key_set": bool(snapshot["previous_key"]),
        "previous_key_expires_at": snapshot["previous_key_expires_at"],
        "invalid_limit": snapshot["invalid_limit"],
        "invalid_window_seconds": snapshot["invalid_window_seconds"],
        "lockout_seconds": snapshot["lockout_seconds"],
    }


def _normalize_expiry(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc).isoformat()
    return value.astimezone(timezone.utc).isoformat()


def enforce_reviewer_key(
    provided_key: str | None,
    action: str,
    actor_ip: str | None,
    resource_type: str = "run",
    resource_id: int | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    key_provided = bool(provided_key)
    actor_key = actor_ip or "unknown"
    now = datetime.now(timezone.utc)
    key_classification = classify_reviewer_key(provided_key, now)

    if key_classification is not None:
        _clear_invalid_attempts(actor_key)
        allowed_details = dict(details or {})
        allowed_details["reviewer_key_type"] = key_classification
        create_reviewer_audit_event(
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            outcome="allowed",
            reviewer_key_provided=key_provided,
            actor_ip=actor_ip,
            details=allowed_details,
        )
        return

    locked, retry_after = _is_reviewer_locked(actor_key, now)
    if locked:
        lockout_details = dict(details or {})
        lockout_details["retry_after_seconds"] = retry_after
        create_reviewer_audit_event(
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            outcome="denied",
            reason="reviewer_lockout_active",
            reviewer_key_provided=key_provided,
            actor_ip=actor_ip,
            details=lockout_details,
        )
        raise HTTPException(
            status_code=429,
            detail=f"Too many invalid reviewer authorization attempts. Try again in {retry_after}s.",
        )

    _register_invalid_attempt(actor_key, now)
    create_reviewer_audit_event(
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        outcome="denied",
        reason="invalid_or_missing_reviewer_key",
        reviewer_key_provided=key_provided,
        actor_ip=actor_ip,
        details=details,
    )
    raise HTTPException(status_code=403, detail="Reviewer authorization required")


def is_default_policy(policy: PolicyPayload) -> bool:
    default_policy = PolicyPayload()
    return policy.model_dump() == default_policy.model_dump()


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/runs")
def list_runs() -> list[dict[str, Any]]:
    runs_df = get_runs()
    return runs_df.to_dict(orient="records")


@app.get("/runs/{run_id}")
def get_run_detail(run_id: int) -> dict[str, Any]:
    runs_df = get_runs()
    run_rows = runs_df[runs_df["id"] == run_id]
    if run_rows.empty:
        raise HTTPException(status_code=404, detail=f"Run {run_id} not found")

    run = run_rows.iloc[0]
    events_df = get_run_events(run_id)
    failures_df = get_run_failures(run_id)
    samples_df = get_run_samples(run_id)
    summary = build_run_summary(run, events_df, failures_df)
    policy_row = get_run_policy_assessment(run_id)
    policy_assessment = None
    if policy_row is not None:
        policy_assessment = {
            "snapshot_id": policy_row.get("snapshot_id"),
            "version": policy_row.get("version"),
            "policy": json.loads(policy_row["policy_json"]),
            "verdict": policy_row["verdict"],
            "summary": json.loads(policy_row["summary_json"]),
            "evaluated_at": policy_row["evaluated_at"],
        }
        summary_policy = dict(policy_assessment["summary"])
        summary_policy["snapshot_id"] = policy_assessment["snapshot_id"]
        summary_policy["version"] = policy_assessment["version"]
        summary_policy["evaluated_at"] = policy_assessment["evaluated_at"]
        summary = build_run_summary(
            run,
            events_df,
            failures_df,
            policy_assessment=summary_policy,
        )

    return {
        "summary": summary,
        "event_count": int(len(events_df)),
        "failure_count": int(len(failures_df)),
        "sample_count": int(len(samples_df)),
        "policy_assessment": policy_assessment,
    }


@app.delete("/runs/{run_id}")
def delete_run_by_id(
    request: Request,
    run_id: int,
    x_reviewer_key: str | None = Header(default=None, alias="x-reviewer-key"),
) -> dict[str, Any]:
    enforce_reviewer_key(
        x_reviewer_key,
        action="delete_run",
        actor_ip=request.client.host if request.client is not None else None,
        resource_id=run_id,
    )
    deleted = delete_run(run_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Run {run_id} not found")
    return {"deleted": True, "run_id": run_id}


@app.get("/audit/reviewer-events")
def get_reviewer_events(
    request: Request,
    limit: int = 100,
    x_reviewer_key: str | None = Header(default=None, alias="x-reviewer-key"),
) -> list[dict[str, Any]]:
    enforce_reviewer_key(
        x_reviewer_key,
        action="read_reviewer_audit_events",
        actor_ip=request.client.host if request.client is not None else None,
        resource_type="audit",
        resource_id=None,
        details={"limit": limit},
    )
    return list_reviewer_audit_events(limit)


@app.get("/admin/reviewer-auth")
def get_reviewer_auth(
    request: Request,
    x_reviewer_key: str | None = Header(default=None, alias="x-reviewer-key"),
) -> dict[str, Any]:
    enforce_reviewer_key(
        x_reviewer_key,
        action="read_reviewer_auth_settings",
        actor_ip=request.client.host if request.client is not None else None,
        resource_type="admin",
        resource_id=None,
    )
    return _redact_reviewer_auth_snapshot(get_current_reviewer_auth_snapshot())


@app.patch("/admin/reviewer-auth")
def update_reviewer_auth(
    request: Request,
    payload: ReviewerAuthUpdatePayload,
    x_reviewer_key: str | None = Header(default=None, alias="x-reviewer-key"),
) -> dict[str, Any]:
    enforce_reviewer_key(
        x_reviewer_key,
        action="update_reviewer_auth_settings",
        actor_ip=request.client.host if request.client is not None else None,
        resource_type="admin",
        resource_id=None,
        details={"fields": sorted(payload.model_fields_set)},
    )

    current = get_current_reviewer_auth_snapshot()

    if "active_key" in payload.model_fields_set and payload.active_key is None:
        raise HTTPException(status_code=400, detail="active_key cannot be null")

    active_key = payload.active_key if payload.active_key is not None else str(current["active_key"])
    previous_key = payload.previous_key if "previous_key" in payload.model_fields_set else current["previous_key"]
    if "previous_key" in payload.model_fields_set and payload.previous_key == "":
        previous_key = None

    previous_key_expires_at = current["previous_key_expires_at"]
    if "previous_key_expires_at" in payload.model_fields_set:
        previous_key_expires_at = _normalize_expiry(payload.previous_key_expires_at)

    invalid_limit = int(payload.invalid_limit if payload.invalid_limit is not None else current["invalid_limit"])
    invalid_window_seconds = int(
        payload.invalid_window_seconds if payload.invalid_window_seconds is not None else current["invalid_window_seconds"]
    )
    lockout_seconds = int(payload.lockout_seconds if payload.lockout_seconds is not None else current["lockout_seconds"])

    upsert_reviewer_auth_settings(
        active_key=active_key,
        previous_key=previous_key,
        previous_key_expires_at=previous_key_expires_at,
        invalid_limit=invalid_limit,
        invalid_window_seconds=invalid_window_seconds,
        lockout_seconds=lockout_seconds,
    )

    reset_reviewer_guard_state()
    updated = get_current_reviewer_auth_snapshot()
    return {"updated": True, "settings": _redact_reviewer_auth_snapshot(updated)}


@app.get("/runs/{run_id}/policy-history")
def get_run_policy_history(run_id: int) -> list[dict[str, Any]]:
    runs_df = get_runs()
    run_rows = runs_df[runs_df["id"] == run_id]
    if run_rows.empty:
        raise HTTPException(status_code=404, detail=f"Run {run_id} not found")

    snapshots = list_run_policy_snapshots(run_id)
    history: list[dict[str, Any]] = []
    for snapshot in snapshots:
        history.append(
            {
                "snapshot_id": snapshot["snapshot_id"],
                "version": snapshot["version"],
                "verdict": snapshot["verdict"],
                "policy": json.loads(snapshot["policy_json"]),
                "summary": json.loads(snapshot["summary_json"]),
                "evaluated_at": snapshot["evaluated_at"],
            }
        )
    return history


@app.post("/ingest-json")
def ingest_json(
    request: Request,
    payload: IngestPayload,
    x_reviewer_key: str | None = Header(default=None, alias="x-reviewer-key"),
) -> dict[str, Any]:
    if not payload.records:
        raise HTTPException(status_code=400, detail="records cannot be empty")

    if not is_default_policy(payload.policy):
        enforce_reviewer_key(
            x_reviewer_key,
            action="ingest_custom_policy",
            actor_ip=request.client.host if request.client is not None else None,
            resource_type="ingest",
            resource_id=None,
            details={"run_name": payload.run_name, "source_file": payload.source_file},
        )

    raw_df = pd.DataFrame(payload.records)
    normalized = normalize_log_dataframe(raw_df, source=payload.source_file)

    threshold_model = DetectionThresholds(
        harsh_brake_mps2=payload.thresholds.harsh_brake_mps2,
        hard_accel_mps2=payload.thresholds.hard_accel_mps2,
        aggressive_steering_deg=payload.thresholds.aggressive_steering_deg,
        speeding_mps=payload.thresholds.speeding_mps,
        cut_in_drop_m=payload.thresholds.cut_in_drop_m,
    )

    run_id = create_test_run(payload.run_name, payload.source_file, normalized)
    events = detect_events(normalized, thresholds=threshold_model)
    insert_events(run_id, events)

    policy_config = PolicyConfig(
        max_cut_in_events=payload.policy.max_cut_in_events,
        max_speeding_events=payload.policy.max_speeding_events,
        max_stop_sign_violations=payload.policy.max_stop_sign_violations,
        max_braking_events=payload.policy.max_braking_events,
        max_aggressive_steering_events=payload.policy.max_aggressive_steering_events,
        max_hard_accel_events=payload.policy.max_hard_accel_events,
    )
    events_df = pd.DataFrame(events)
    policy_assessment = evaluate_policy(events_df, policy_config)
    upsert_run_policy_assessment(
        run_id,
        policy_json=json.dumps(policy_config.__dict__),
        verdict=policy_assessment.verdict,
        summary_json=json.dumps(policy_assessment.to_dict()),
    )

    auto_failures = build_policy_failures(policy_assessment)
    for reason in auto_failures:
        create_failure(run_id=run_id, reason=reason)

    return {
        "run_id": run_id,
        "rows_ingested": len(normalized),
        "events_detected": len(events),
        "policy_verdict": policy_assessment.verdict,
        "policy_failures_created": len(auto_failures),
    }
