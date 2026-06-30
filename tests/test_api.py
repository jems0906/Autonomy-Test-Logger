import pytest
from fastapi.testclient import TestClient

from app.api import app, reset_reviewer_guard_state
from app.db import clear_reviewer_auth_settings

client = TestClient(app)


@pytest.fixture(autouse=True)
def reset_reviewer_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ATL_REVIEWER_KEY", "reviewer")
    monkeypatch.delenv("ATL_REVIEWER_PREVIOUS_KEY", raising=False)
    monkeypatch.delenv("ATL_REVIEWER_PREVIOUS_KEY_EXPIRES_AT", raising=False)
    monkeypatch.delenv("ATL_REVIEWER_INVALID_LIMIT", raising=False)
    monkeypatch.delenv("ATL_REVIEWER_INVALID_WINDOW_SECONDS", raising=False)
    monkeypatch.delenv("ATL_REVIEWER_LOCKOUT_SECONDS", raising=False)
    clear_reviewer_auth_settings()
    reset_reviewer_guard_state()


def test_health() -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_ingest_and_fetch_runs() -> None:
    payload = {
        "run_name": "api-test-run",
        "source_file": "api.json",
        "records": [
            {
                "time": "2026-06-30T09:00:00Z",
                "vehicle_speed": 12.0,
                "steer": 0.1,
                "acceleration": 0.2,
                "lane": "center",
                "distance_to_object_m": 35.0,
                "stop_sign": False,
            },
            {
                "time": "2026-06-30T09:00:00.2Z",
                "vehicle_speed": 29.0,
                "steer": 16.0,
                "acceleration": 3.3,
                "lane": "left",
                "distance_to_object_m": 20.0,
                "stop_sign": False,
            },
        ],
    }

    ingest = client.post("/ingest-json", json=payload)
    assert ingest.status_code == 200
    run_id = ingest.json()["run_id"]
    assert ingest.json()["policy_verdict"] in {"pass", "fail"}

    runs = client.get("/runs")
    assert runs.status_code == 200
    assert any(r["id"] == run_id for r in runs.json())

    detail = client.get(f"/runs/{run_id}")
    assert detail.status_code == 200
    body = detail.json()
    assert body["sample_count"] == 2
    assert body["policy_assessment"] is not None
    assert "summary" in body


def test_delete_run() -> None:
    payload = {
        "run_name": "api-delete-run",
        "source_file": "api.json",
        "records": [
            {
                "time": "2026-06-30T09:01:00Z",
                "vehicle_speed": 5.0,
                "steer": 0.0,
                "acceleration": 0.0,
                "lane": "center",
                "distance_to_object_m": 50.0,
                "stop_sign": False,
            }
        ],
    }

    ingest = client.post("/ingest-json", json=payload)
    assert ingest.status_code == 200
    run_id = ingest.json()["run_id"]

    delete_response = client.delete(f"/runs/{run_id}", headers={"x-reviewer-key": "reviewer"})
    assert delete_response.status_code == 200
    assert delete_response.json() == {"deleted": True, "run_id": run_id}

    detail_after_delete = client.get(f"/runs/{run_id}")
    assert detail_after_delete.status_code == 404


def test_policy_history_endpoint() -> None:
    payload = {
        "run_name": "api-policy-history",
        "source_file": "api.json",
        "records": [
            {
                "time": "2026-06-30T09:05:00Z",
                "vehicle_speed": 31.0,
                "steer": 18.0,
                "acceleration": 3.2,
                "lane": "left",
                "distance_to_object_m": 20.0,
                "stop_sign": False,
            }
        ],
    }

    ingest = client.post("/ingest-json", json=payload)
    assert ingest.status_code == 200
    run_id = ingest.json()["run_id"]

    history = client.get(f"/runs/{run_id}/policy-history")
    assert history.status_code == 200
    body = history.json()
    assert isinstance(body, list)
    assert len(body) >= 1
    assert body[0]["version"] >= 1


def test_delete_requires_reviewer_key() -> None:
    payload = {
        "run_name": "api-delete-auth",
        "source_file": "api.json",
        "records": [
            {
                "time": "2026-06-30T09:07:00Z",
                "vehicle_speed": 8.0,
                "steer": 0.0,
                "acceleration": 0.0,
                "lane": "center",
                "distance_to_object_m": 50.0,
                "stop_sign": False,
            }
        ],
    }

    ingest = client.post("/ingest-json", json=payload)
    assert ingest.status_code == 200
    run_id = ingest.json()["run_id"]

    denied = client.delete(f"/runs/{run_id}")
    assert denied.status_code == 403


def test_custom_policy_ingest_requires_reviewer_key() -> None:
    payload = {
        "run_name": "api-policy-auth",
        "source_file": "api.json",
        "records": [
            {
                "time": "2026-06-30T09:08:00Z",
                "vehicle_speed": 12.0,
                "steer": 0.0,
                "acceleration": 0.0,
                "lane": "center",
                "distance_to_object_m": 50.0,
                "stop_sign": False,
            }
        ],
        "policy": {
            "max_cut_in_events": 1,
            "max_speeding_events": 1,
            "max_stop_sign_violations": 0,
            "max_braking_events": 2,
            "max_aggressive_steering_events": 3,
            "max_hard_accel_events": 3,
        },
    }

    denied = client.post("/ingest-json", json=payload)
    assert denied.status_code == 403

    allowed = client.post("/ingest-json", json=payload, headers={"x-reviewer-key": "reviewer"})
    assert allowed.status_code == 200


def test_reviewer_audit_events_recorded_and_protected() -> None:
    custom_policy_payload = {
        "run_name": "api-audit-policy-auth",
        "source_file": "api.json",
        "records": [
            {
                "time": "2026-06-30T09:09:00Z",
                "vehicle_speed": 10.0,
                "steer": 0.0,
                "acceleration": 0.0,
                "lane": "center",
                "distance_to_object_m": 45.0,
                "stop_sign": False,
            }
        ],
        "policy": {
            "max_cut_in_events": 1,
            "max_speeding_events": 1,
            "max_stop_sign_violations": 0,
            "max_braking_events": 2,
            "max_aggressive_steering_events": 3,
            "max_hard_accel_events": 3,
        },
    }

    denied_ingest = client.post("/ingest-json", json=custom_policy_payload)
    assert denied_ingest.status_code == 403

    denied_delete = client.delete("/runs/999999")
    assert denied_delete.status_code == 403

    audit_denied = client.get("/audit/reviewer-events")
    assert audit_denied.status_code == 403

    audit_allowed = client.get("/audit/reviewer-events?limit=200", headers={"x-reviewer-key": "reviewer"})
    assert audit_allowed.status_code == 200
    events = audit_allowed.json()

    assert any(
        e["action"] == "ingest_custom_policy"
        and e["outcome"] == "denied"
        and e["reason"] == "invalid_or_missing_reviewer_key"
        for e in events
    )
    assert any(
        e["action"] == "delete_run"
        and e["outcome"] == "denied"
        and e["reason"] == "invalid_or_missing_reviewer_key"
        for e in events
    )

    allowed_delete = client.delete("/runs/999999", headers={"x-reviewer-key": "reviewer"})
    assert allowed_delete.status_code == 404

    audit_after_allowed = client.get("/audit/reviewer-events?limit=200", headers={"x-reviewer-key": "reviewer"})
    assert audit_after_allowed.status_code == 200
    events_after = audit_after_allowed.json()
    assert any(
        e["action"] == "delete_run"
        and e["outcome"] == "allowed"
        and e["reviewer_key_provided"] is True
        for e in events_after
    )


def test_reviewer_invalid_attempt_lockout(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ATL_REVIEWER_INVALID_LIMIT", "2")
    monkeypatch.setenv("ATL_REVIEWER_INVALID_WINDOW_SECONDS", "300")
    monkeypatch.setenv("ATL_REVIEWER_LOCKOUT_SECONDS", "120")
    reset_reviewer_guard_state()

    first_denied = client.delete("/runs/999999")
    assert first_denied.status_code == 403

    second_denied = client.delete("/runs/999999", headers={"x-reviewer-key": "wrong"})
    assert second_denied.status_code == 403

    locked = client.delete("/runs/999999", headers={"x-reviewer-key": "wrong"})
    assert locked.status_code == 429
    assert "Too many invalid reviewer authorization attempts" in locked.json()["detail"]

    allowed_with_correct_key = client.delete("/runs/999999", headers={"x-reviewer-key": "reviewer"})
    assert allowed_with_correct_key.status_code == 404


def test_previous_reviewer_key_allowed_within_rotation_window(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ATL_REVIEWER_KEY", "reviewer-new")
    monkeypatch.setenv("ATL_REVIEWER_PREVIOUS_KEY", "reviewer-old")
    monkeypatch.setenv("ATL_REVIEWER_PREVIOUS_KEY_EXPIRES_AT", "2999-01-01T00:00:00+00:00")
    reset_reviewer_guard_state()

    allowed = client.delete("/runs/999999", headers={"x-reviewer-key": "reviewer-old"})
    assert allowed.status_code == 404

    audit_events = client.get(
        "/audit/reviewer-events?limit=50",
        headers={"x-reviewer-key": "reviewer-new"},
    )
    assert audit_events.status_code == 200
    assert any(
        e["action"] == "delete_run"
        and e["outcome"] == "allowed"
        and e["details"].get("reviewer_key_type") == "previous"
        for e in audit_events.json()
    )


def test_previous_reviewer_key_denied_after_rotation_window(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ATL_REVIEWER_KEY", "reviewer-new")
    monkeypatch.setenv("ATL_REVIEWER_PREVIOUS_KEY", "reviewer-old")
    monkeypatch.setenv("ATL_REVIEWER_PREVIOUS_KEY_EXPIRES_AT", "2000-01-01T00:00:00+00:00")
    reset_reviewer_guard_state()

    denied = client.delete("/runs/999999", headers={"x-reviewer-key": "reviewer-old"})
    assert denied.status_code == 403

    allowed_with_active = client.delete("/runs/999999", headers={"x-reviewer-key": "reviewer-new"})
    assert allowed_with_active.status_code == 404


def test_admin_reviewer_auth_patch_persists_rotation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ATL_REVIEWER_KEY", "reviewer-old")
    reset_reviewer_guard_state()

    update_payload = {
        "active_key": "reviewer-new",
        "previous_key": "reviewer-old",
        "previous_key_expires_at": "2999-01-01T00:00:00Z",
        "invalid_limit": 3,
        "invalid_window_seconds": 600,
        "lockout_seconds": 90,
    }

    update_response = client.patch(
        "/admin/reviewer-auth",
        json=update_payload,
        headers={"x-reviewer-key": "reviewer-old"},
    )
    assert update_response.status_code == 200
    assert update_response.json()["settings"]["active_key_set"] is True
    assert update_response.json()["settings"]["previous_key_set"] is True

    monkeypatch.setenv("ATL_REVIEWER_KEY", "some-other-value")
    monkeypatch.setenv("ATL_REVIEWER_PREVIOUS_KEY", "")
    monkeypatch.setenv("ATL_REVIEWER_PREVIOUS_KEY_EXPIRES_AT", "")

    delete_with_new = client.delete("/runs/999999", headers={"x-reviewer-key": "reviewer-new"})
    assert delete_with_new.status_code == 404

    audit_response = client.get("/admin/reviewer-auth", headers={"x-reviewer-key": "reviewer-new"})
    assert audit_response.status_code == 200
    assert audit_response.json()["active_key_set"] is True
    assert audit_response.json()["previous_key_set"] is True
