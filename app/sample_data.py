from __future__ import annotations

import numpy as np
import pandas as pd


def generate_sample_run(rows: int = 600, seed: int = 7) -> pd.DataFrame:
    rng = np.random.default_rng(seed)

    ts = pd.date_range(start=pd.Timestamp.utcnow().floor("s"), periods=rows, freq="200ms")
    speed = np.clip(14 + np.cumsum(rng.normal(0, 0.08, size=rows)), 0, 35)
    steering = np.clip(rng.normal(0, 3, size=rows), -24, 24)
    lane = np.full(rows, "center", dtype=object)

    # Programmed lane changes and merges for realistic tags.
    lane[130:260] = "left"
    lane[260:400] = "center"
    lane[400:520] = "right"

    steering[124:137] += np.linspace(0, 16, 13)
    steering[252:265] -= np.linspace(0, 14, 13)
    steering[393:410] += np.linspace(0, 11, 17)

    distance_to_lead = np.clip(45 + rng.normal(0, 1.8, size=rows), 5, 200)

    # Simulate cut-ins with abrupt lead distance drops.
    distance_to_lead[210:216] -= np.linspace(0, 18, 6)
    distance_to_lead[468:474] -= np.linspace(0, 12, 6)

    # Simulate braking near a stop sign.
    speed[520:560] = np.linspace(speed[519], 0.15, 40)
    speed[560:585] = np.linspace(0.15, 8.0, 25)

    acceleration = np.gradient(speed, 0.2)

    stop_sign = np.zeros(rows, dtype=bool)
    stop_sign[535:555] = True

    return pd.DataFrame(
        {
            "ts": ts,
            "speed_mps": speed,
            "steering_deg": steering,
            "acceleration_mps2": acceleration,
            "lane_id": lane,
            "distance_to_lead_m": distance_to_lead,
            "stop_sign_detected": stop_sign,
        }
    )
