import pandas as pd

from app.ingestion import normalize_log_dataframe, parse_uploaded_bytes


def test_parse_csv_aliases_and_types() -> None:
    content = """timestamp,speed,steer,accel,lane,lead_distance,stop_sign
2026-06-30T09:00:00Z,10.1,1.2,0.1,center,30,false
2026-06-30T09:00:00.2Z,10.4,2.0,1.5,left,22,true
"""
    df = parse_uploaded_bytes("run.csv", content.encode("utf-8"))

    assert not df.empty
    assert {"ts", "speed_mps", "steering_deg", "acceleration_mps2", "lane_id", "distance_to_lead_m", "stop_sign_detected"}.issubset(df.columns)
    assert pd.api.types.is_datetime64_any_dtype(df["ts"])
    assert df["stop_sign_detected"].tolist() == [False, True]


def test_parse_json_with_records_wrapper() -> None:
    payload = (
        '{"records": ['
        '{"time":"2026-06-30T10:00:00Z","vehicle_speed":12.0,"steer":0.3},'
        '{"time":"2026-06-30T10:00:00.2Z","vehicle_speed":12.5,"steer":0.8}'
        ']}'
    )

    df = parse_uploaded_bytes("run.json", payload.encode("utf-8"))

    assert len(df) == 2
    assert "speed_mps" in df.columns
    assert "steering_deg" in df.columns


def test_normalize_fills_missing_columns() -> None:
    raw = pd.DataFrame({"timestamp": ["2026-06-30T10:00:00Z"], "speed": [8.0]})
    df = normalize_log_dataframe(raw, source="unit")

    assert len(df) == 1
    assert df.loc[0, "speed_mps"] == 8.0
    assert df.loc[0, "lane_id"] == "unknown"
    assert not df.loc[0, "stop_sign_detected"]
