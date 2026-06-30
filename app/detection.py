from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd


@dataclass(frozen=True)
class DetectionThresholds:
    harsh_brake_mps2: float = -2.8
    hard_accel_mps2: float = 2.8
    aggressive_steering_deg: float = 15.0
    speeding_mps: float = 27.0
    cut_in_drop_m: float = 8.0


def detect_events(df: pd.DataFrame, thresholds: DetectionThresholds | None = None) -> list[dict[str, Any]]:
    thresholds = thresholds or DetectionThresholds()
    events: list[dict[str, Any]] = []

    work = df.copy()
    work["dt_s"] = work["ts"].diff().dt.total_seconds().fillna(0.0)
    work["steering_rate"] = work["steering_deg"].diff().fillna(0.0) / work["dt_s"].replace(0, 1.0)
    work["lead_drop"] = work["distance_to_lead_m"].diff().fillna(0.0)
    work["lane_changed"] = work["lane_id"].astype(str).ne(work["lane_id"].shift(1).astype(str))

    for idx, row in work.iterrows():
        ts = row["ts"]

        if bool(row["lane_changed"]) and idx > 0:
            prev_lane = str(work.loc[idx - 1, "lane_id"])
            curr_lane = str(row["lane_id"])
            events.append(
                make_event(
                    "lane_change",
                    ts,
                    "medium",
                    {
                        "from_lane": prev_lane,
                        "to_lane": curr_lane,
                        "steering_deg": row["steering_deg"],
                    },
                )
            )

            # A merge is approximated as a lane change with sustained steering input and moderate speed.
            if abs(float(row["steering_deg"])) > 6.0 and float(row["speed_mps"]) > 8.0:
                events.append(
                    make_event(
                        "merge",
                        ts,
                        "medium",
                        {
                            "from_lane": prev_lane,
                            "to_lane": curr_lane,
                            "speed_mps": row["speed_mps"],
                        },
                    )
                )

        if float(row["acceleration_mps2"]) <= thresholds.harsh_brake_mps2 and float(row["speed_mps"]) > 5.0:
            events.append(
                make_event(
                    "braking",
                    ts,
                    "high",
                    {
                        "acceleration_mps2": row["acceleration_mps2"],
                        "speed_mps": row["speed_mps"],
                    },
                )
            )

        if float(row["acceleration_mps2"]) >= thresholds.hard_accel_mps2:
            events.append(
                make_event(
                    "hard_acceleration",
                    ts,
                    "medium",
                    {
                        "acceleration_mps2": row["acceleration_mps2"],
                        "speed_mps": row["speed_mps"],
                    },
                )
            )

        if abs(float(row["steering_deg"])) >= thresholds.aggressive_steering_deg:
            events.append(
                make_event(
                    "aggressive_steering",
                    ts,
                    "medium",
                    {
                        "steering_deg": row["steering_deg"],
                        "speed_mps": row["speed_mps"],
                    },
                )
            )

        if float(row["speed_mps"]) >= thresholds.speeding_mps:
            events.append(
                make_event(
                    "speed_threshold_exceeded",
                    ts,
                    "high",
                    {
                        "speed_mps": row["speed_mps"],
                        "threshold_mps": thresholds.speeding_mps,
                    },
                )
            )

        # Cut-in heuristic: sudden drop in distance to lead without heavy braking from ego vehicle.
        if float(row["lead_drop"]) <= -thresholds.cut_in_drop_m and float(row["acceleration_mps2"]) > -1.0:
            events.append(
                make_event(
                    "cut_in",
                    ts,
                    "high",
                    {
                        "distance_to_lead_m": row["distance_to_lead_m"],
                        "distance_drop_m": row["lead_drop"],
                    },
                )
            )

        if bool(row["stop_sign_detected"]) and float(row["speed_mps"]) < 0.5:
            events.append(
                make_event(
                    "stop_sign_compliance",
                    ts,
                    "low",
                    {
                        "speed_mps": row["speed_mps"],
                    },
                )
            )

        if bool(row["stop_sign_detected"]) and float(row["speed_mps"]) >= 1.5:
            events.append(
                make_event(
                    "stop_sign_violation",
                    ts,
                    "critical",
                    {
                        "speed_mps": row["speed_mps"],
                    },
                )
            )

    return dedupe_nearby_events(events)


def dedupe_nearby_events(events: list[dict[str, Any]], min_gap_seconds: float = 0.75) -> list[dict[str, Any]]:
    if not events:
        return []

    sorted_events = sorted(events, key=lambda e: (e["event_type"], e["ts"]))
    deduped: list[dict[str, Any]] = []
    last_by_type: dict[str, pd.Timestamp] = {}

    for event in sorted_events:
        event_ts = pd.to_datetime(event["ts"])
        last_ts = last_by_type.get(event["event_type"])
        if last_ts is not None and (event_ts - last_ts).total_seconds() < min_gap_seconds:
            continue
        deduped.append(event)
        last_by_type[event["event_type"]] = event_ts

    return sorted(deduped, key=lambda e: e["ts"])


def make_event(event_type: str, ts: pd.Timestamp, severity: str, details: dict[str, Any]) -> dict[str, Any]:
    return {
        "event_type": event_type,
        "ts": ts.isoformat(),
        "severity": severity,
        "details": details,
    }
