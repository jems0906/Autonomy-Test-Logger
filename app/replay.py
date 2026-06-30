from __future__ import annotations

import pandas as pd


def build_event_window(samples_df: pd.DataFrame, event_ts: pd.Timestamp, window_seconds: float) -> pd.DataFrame:
    """Return samples in a symmetric time window around an event timestamp."""
    if samples_df.empty:
        return samples_df.copy()

    start = event_ts - pd.to_timedelta(window_seconds, unit="s")
    end = event_ts + pd.to_timedelta(window_seconds, unit="s")

    window_df = samples_df[(samples_df["ts"] >= start) & (samples_df["ts"] <= end)].copy()
    window_df.sort_values("ts", inplace=True)
    window_df.reset_index(drop=True, inplace=True)
    return window_df
