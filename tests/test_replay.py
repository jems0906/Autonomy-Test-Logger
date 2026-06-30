import pandas as pd

from app.replay import build_event_window


def test_build_event_window_returns_symmetric_range() -> None:
    samples_df = pd.DataFrame(
        {
            "ts": pd.to_datetime(
                [
                    "2026-06-30T09:00:00Z",
                    "2026-06-30T09:00:01Z",
                    "2026-06-30T09:00:02Z",
                    "2026-06-30T09:00:03Z",
                    "2026-06-30T09:00:04Z",
                ],
                utc=True,
            ),
            "speed_mps": [1, 2, 3, 4, 5],
        }
    )

    event_ts = pd.Timestamp("2026-06-30T09:00:02Z")
    result = build_event_window(samples_df, event_ts, window_seconds=1.0)

    assert len(result) == 3
    assert result["ts"].iloc[0] == pd.Timestamp("2026-06-30T09:00:01Z")
    assert result["ts"].iloc[-1] == pd.Timestamp("2026-06-30T09:00:03Z")
