import pandas as pd

from app.detection import DetectionThresholds, detect_events


def build_df() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "ts": pd.to_datetime(
                [
                    "2026-06-30T09:00:00Z",
                    "2026-06-30T09:00:00.500Z",
                    "2026-06-30T09:00:01Z",
                    "2026-06-30T09:00:01.500Z",
                    "2026-06-30T09:00:02Z",
                ],
                format="mixed",
                utc=True,
            ),
            "speed_mps": [12.0, 28.0, 9.0, 6.0, 0.2],
            "steering_deg": [0.0, 18.0, 8.0, 1.0, 0.0],
            "acceleration_mps2": [0.0, 3.1, -0.2, -3.2, -0.4],
            "lane_id": ["center", "left", "left", "left", "left"],
            "distance_to_lead_m": [40.0, 39.0, 20.0, 19.5, 19.0],
            "stop_sign_detected": [False, False, False, False, True],
        }
    )


def test_detect_core_events() -> None:
    events = detect_events(build_df())
    event_types = {e["event_type"] for e in events}

    assert "lane_change" in event_types
    assert "merge" in event_types
    assert "braking" in event_types
    assert "hard_acceleration" in event_types
    assert "aggressive_steering" in event_types
    assert "speed_threshold_exceeded" in event_types
    assert "cut_in" in event_types
    assert "stop_sign_compliance" in event_types


def test_threshold_override_reduces_events() -> None:
    strict = DetectionThresholds(speeding_mps=40.0, aggressive_steering_deg=30.0, hard_accel_mps2=5.0)
    events = detect_events(build_df(), thresholds=strict)
    event_types = {e["event_type"] for e in events}

    assert "speed_threshold_exceeded" not in event_types
    assert "aggressive_steering" not in event_types
    assert "hard_acceleration" not in event_types
