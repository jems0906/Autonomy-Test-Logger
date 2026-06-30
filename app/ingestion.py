from __future__ import annotations

import json
from io import StringIO
from typing import Any

import numpy as np
import pandas as pd

REQUIRED_LOGICAL_COLUMNS = [
    "ts",
    "speed_mps",
    "steering_deg",
    "acceleration_mps2",
    "lane_id",
    "distance_to_lead_m",
    "stop_sign_detected",
]

COLUMN_ALIASES = {
    "timestamp": "ts",
    "time": "ts",
    "datetime": "ts",
    "speed": "speed_mps",
    "vehicle_speed": "speed_mps",
    "steering": "steering_deg",
    "steer": "steering_deg",
    "acceleration": "acceleration_mps2",
    "accel": "acceleration_mps2",
    "lane": "lane_id",
    "lead_distance": "distance_to_lead_m",
    "distance_to_object_m": "distance_to_lead_m",
    "stop_sign": "stop_sign_detected",
    "stop_sign_flag": "stop_sign_detected",
}


def parse_uploaded_bytes(file_name: str, raw_bytes: bytes) -> pd.DataFrame:
    lower_name = file_name.lower()
    if lower_name.endswith(".csv"):
        text = raw_bytes.decode("utf-8", errors="ignore")
        df = pd.read_csv(StringIO(text))
    elif lower_name.endswith(".json"):
        text = raw_bytes.decode("utf-8", errors="ignore")
        payload = json.loads(text)
        if isinstance(payload, dict):
            if "records" in payload and isinstance(payload["records"], list):
                payload = payload["records"]
            else:
                payload = [payload]
        df = pd.DataFrame(payload)
    else:
        raise ValueError(f"Unsupported file type for {file_name}. Only CSV and JSON are accepted.")

    return normalize_log_dataframe(df, source=file_name)


def normalize_log_dataframe(df: pd.DataFrame, source: str = "unknown") -> pd.DataFrame:
    normalized = df.copy()
    normalized.columns = [str(c).strip() for c in normalized.columns]
    lowered_map = {c: COLUMN_ALIASES.get(c.lower(), c.lower()) for c in normalized.columns}
    normalized.rename(columns=lowered_map, inplace=True)

    for col in REQUIRED_LOGICAL_COLUMNS:
        if col not in normalized.columns:
            normalized[col] = np.nan

    normalized["raw"] = normalized.to_dict(orient="records")
    normalized["source"] = source

    normalized["ts"] = pd.to_datetime(normalized["ts"], errors="coerce")

    if normalized["ts"].isna().all():
        normalized["ts"] = pd.date_range(start=pd.Timestamp.utcnow(), periods=len(normalized), freq="500ms")
    else:
        normalized["ts"] = normalized["ts"].ffill()
        normalized["ts"] = normalized["ts"].bfill()

    normalized.sort_values("ts", inplace=True)
    normalized.reset_index(drop=True, inplace=True)

    for num_col in ["speed_mps", "steering_deg", "acceleration_mps2", "distance_to_lead_m"]:
        normalized[num_col] = pd.to_numeric(normalized[num_col], errors="coerce")

    normalized["speed_mps"] = normalized["speed_mps"].fillna(0.0)
    normalized["steering_deg"] = normalized["steering_deg"].fillna(0.0)

    if normalized["acceleration_mps2"].isna().all():
        dt_s = normalized["ts"].diff().dt.total_seconds().replace(0, np.nan)
        normalized["acceleration_mps2"] = normalized["speed_mps"].diff() / dt_s
    normalized["acceleration_mps2"] = normalized["acceleration_mps2"].fillna(0.0)

    normalized["distance_to_lead_m"] = normalized["distance_to_lead_m"].ffill()
    normalized["distance_to_lead_m"] = normalized["distance_to_lead_m"].fillna(150.0)

    normalized["lane_id"] = normalized["lane_id"].ffill()
    normalized["lane_id"] = normalized["lane_id"].fillna("unknown")

    normalized["stop_sign_detected"] = normalized["stop_sign_detected"].apply(to_bool)

    return normalized


def to_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return False
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    return text in {"1", "true", "yes", "y", "t", "detected"}
