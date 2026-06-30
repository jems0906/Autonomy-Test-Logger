import json
import zipfile
from io import BytesIO

import pandas as pd

from app.reporting import build_run_summary, export_bundle, summary_to_markdown


def test_build_summary_and_markdown() -> None:
    run = pd.Series(
        {
            "id": 1,
            "name": "run-a",
            "source_file": "log.csv",
            "started_at": "2026-06-30T09:00:00Z",
            "ended_at": "2026-06-30T09:01:00Z",
            "total_rows": 10,
            "created_at": "2026-06-30T09:02:00Z",
        }
    )

    events_df = pd.DataFrame(
        [
            {"event_type": "lane_change", "severity": "medium"},
            {"event_type": "braking", "severity": "high"},
        ]
    )
    failures_df = pd.DataFrame([
        {"status": "open"},
        {"status": "resolved"},
    ])

    summary = build_run_summary(run, events_df, failures_df)
    md = summary_to_markdown(summary)

    assert summary["event_summary"]["total_events"] == 2
    assert summary["failure_summary"]["open_failures"] == 1
    assert "Autonomous Test Run Report" in md


def test_export_bundle_contains_expected_files() -> None:
    summary = {
        "run": {"id": 2, "name": "run-b", "source_file": "x", "started_at": None, "ended_at": None, "total_rows": 0, "created_at": "now"},
        "event_summary": {"total_events": 0, "by_type": {}, "by_severity": {}},
        "failure_summary": {"total_failures": 0, "open_failures": 0, "resolved_failures": 0},
    }
    events_df = pd.DataFrame()
    failures_df = pd.DataFrame()

    blob = export_bundle(summary, events_df, failures_df)
    with zipfile.ZipFile(BytesIO(blob), mode="r") as zf:
        names = set(zf.namelist())
        assert {"summary.json", "summary.md", "events.csv", "failures.csv"}.issubset(names)

        summary_json = json.loads(zf.read("summary.json").decode("utf-8"))
        assert summary_json["run"]["id"] == 2
