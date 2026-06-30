from __future__ import annotations

import json
import os
from datetime import datetime

import pandas as pd
import plotly.express as px
import streamlit as st

from app.db import (
    create_failure,
    create_test_run,
    delete_run,
    get_run_events,
    get_run_failures,
    get_run_policy_assessment,
    get_run_samples,
    get_runs,
    init_db,
    insert_events,
    list_run_policy_snapshots,
    update_failure_status,
    upsert_run_policy_assessment,
)
from app.detection import DetectionThresholds, detect_events
from app.ingestion import parse_uploaded_bytes
from app.policy import PolicyConfig, build_policy_failures, evaluate_policy
from app.replay import build_event_window
from app.reporting import build_run_summary, export_bundle, summary_to_markdown
from app.sample_data import generate_sample_run

st.set_page_config(page_title="Autonomy Test Logger", layout="wide")
init_db()


st.title("Autonomy Test Logger")
st.caption("Ingest driving logs, detect scenario events, and review test failures.")

if "reviewer_mode" not in st.session_state:
    st.session_state.reviewer_mode = False

with st.sidebar:
    st.header("Access")
    access_mode = st.radio("Mode", options=["Viewer", "Reviewer"], horizontal=True)
    reviewer_code = st.text_input("Reviewer Code", type="password")
    expected_reviewer_code = os.getenv("ATL_REVIEWER_CODE", "reviewer")
    if access_mode == "Reviewer" and reviewer_code and reviewer_code == expected_reviewer_code:
        st.session_state.reviewer_mode = True
    elif access_mode == "Viewer":
        st.session_state.reviewer_mode = False

    if st.session_state.reviewer_mode:
        st.success("Reviewer mode enabled")
    else:
        st.info("Viewer mode (policy edit and run delete disabled)")

    st.divider()

    st.header("Ingestion")
    run_name = st.text_input("Run Name", value=f"test-run-{datetime.utcnow().strftime('%Y%m%d-%H%M%S')}")
    st.subheader("Detection Thresholds")
    harsh_brake_mps2 = st.slider("Harsh Brake <= (m/s^2)", min_value=-6.0, max_value=-0.5, value=-2.8, step=0.1)
    hard_accel_mps2 = st.slider("Hard Accel >= (m/s^2)", min_value=0.5, max_value=6.0, value=2.8, step=0.1)
    aggressive_steering_deg = st.slider("Aggressive Steering >= (deg)", min_value=5.0, max_value=35.0, value=15.0, step=0.5)
    speeding_mps = st.slider("Speeding >= (m/s)", min_value=5.0, max_value=45.0, value=27.0, step=0.5)
    cut_in_drop_m = st.slider("Cut-in Distance Drop <= (m)", min_value=2.0, max_value=20.0, value=8.0, step=0.5)

    active_thresholds = DetectionThresholds(
        harsh_brake_mps2=harsh_brake_mps2,
        hard_accel_mps2=hard_accel_mps2,
        aggressive_steering_deg=aggressive_steering_deg,
        speeding_mps=speeding_mps,
        cut_in_drop_m=cut_in_drop_m,
    )

    st.subheader("Policy Rules (Pass/Fail)")
    policy_disabled = not st.session_state.reviewer_mode
    max_cut_in_events = st.number_input(
        "Max Cut-ins", min_value=0, max_value=20, value=0, step=1, disabled=policy_disabled
    )
    max_speeding_events = st.number_input(
        "Max Speeding Events", min_value=0, max_value=20, value=0, step=1, disabled=policy_disabled
    )
    max_stop_sign_violations = st.number_input(
        "Max Stop Sign Violations", min_value=0, max_value=20, value=0, step=1, disabled=policy_disabled
    )
    max_braking_events = st.number_input(
        "Max Braking Events", min_value=0, max_value=50, value=2, step=1, disabled=policy_disabled
    )
    max_aggressive_steering_events = st.number_input(
        "Max Aggressive Steering Events", min_value=0, max_value=50, value=3, step=1, disabled=policy_disabled
    )
    max_hard_accel_events = st.number_input(
        "Max Hard Acceleration Events", min_value=0, max_value=50, value=3, step=1, disabled=policy_disabled
    )

    active_policy = PolicyConfig(
        max_cut_in_events=int(max_cut_in_events),
        max_speeding_events=int(max_speeding_events),
        max_stop_sign_violations=int(max_stop_sign_violations),
        max_braking_events=int(max_braking_events),
        max_aggressive_steering_events=int(max_aggressive_steering_events),
        max_hard_accel_events=int(max_hard_accel_events),
    )

    uploaded_files = st.file_uploader(
        "Upload CSV/JSON logs",
        type=["csv", "json"],
        accept_multiple_files=True,
    )

    if st.button("Ingest Uploaded Files", type="primary", use_container_width=True):
        if not uploaded_files:
            st.warning("Upload at least one file before ingestion.")
        else:
            ingested = 0
            for upload in uploaded_files:
                raw_bytes = upload.getvalue()
                df = parse_uploaded_bytes(upload.name, raw_bytes)
                run_id = create_test_run(run_name, upload.name, df)
                events = detect_events(df, thresholds=active_thresholds)
                insert_events(run_id, events)

                events_df_for_policy = pd.DataFrame(events)
                policy_eval = evaluate_policy(events_df_for_policy, active_policy)
                upsert_run_policy_assessment(
                    run_id,
                    policy_json=json.dumps(active_policy.__dict__),
                    verdict=policy_eval.verdict,
                    summary_json=json.dumps(policy_eval.to_dict()),
                )
                for reason in build_policy_failures(policy_eval):
                    create_failure(run_id=run_id, reason=reason)

                ingested += 1
            st.success(f"Ingested {ingested} file(s) and generated scenario tags.")

    st.divider()

    if st.button("Generate and Ingest Synthetic Sample", use_container_width=True):
        sample_df = generate_sample_run()
        synthetic_name = f"synthetic-{datetime.utcnow().strftime('%H%M%S')}"
        run_id = create_test_run(synthetic_name, "generated_sample", sample_df)
        synthetic_events = detect_events(sample_df, thresholds=active_thresholds)
        insert_events(run_id, synthetic_events)

        events_df_for_policy = pd.DataFrame(synthetic_events)
        policy_eval = evaluate_policy(events_df_for_policy, active_policy)
        upsert_run_policy_assessment(
            run_id,
            policy_json=json.dumps(active_policy.__dict__),
            verdict=policy_eval.verdict,
            summary_json=json.dumps(policy_eval.to_dict()),
        )
        for reason in build_policy_failures(policy_eval):
            create_failure(run_id=run_id, reason=reason)

        st.success(f"Added synthetic run with ID {run_id}.")

runs_df = get_runs()

if runs_df.empty:
    st.info("No runs available yet. Upload CSV/JSON logs or generate a synthetic sample run.")
    st.stop()

left, right = st.columns([1, 2])
with left:
    selected_run_id = st.selectbox(
        "Select Test Run",
        options=runs_df["id"].tolist(),
        format_func=lambda run_id: f"Run {run_id}: {runs_df.loc[runs_df['id'] == run_id, 'name'].iloc[0]}",
    )

selected_run = runs_df.loc[runs_df["id"] == selected_run_id].iloc[0]
samples_df = get_run_samples(int(selected_run_id))
events_df = get_run_events(int(selected_run_id))
failures_df = get_run_failures(int(selected_run_id))
policy_row = get_run_policy_assessment(int(selected_run_id))
policy_assessment_data: dict[str, object] | None = None
policy_history = list_run_policy_snapshots(int(selected_run_id))
if policy_row is not None:
    parsed = json.loads(policy_row["summary_json"])
    if isinstance(parsed, dict):
        parsed["snapshot_id"] = policy_row.get("snapshot_id")
        parsed["version"] = policy_row.get("version")
        parsed["evaluated_at"] = policy_row.get("evaluated_at")
        policy_assessment_data = parsed

if not samples_df.empty:
    samples_df["ts"] = pd.to_datetime(samples_df["ts"])

if not events_df.empty:
    events_df["ts"] = pd.to_datetime(events_df["ts"])
    events_df["details"] = events_df["details_json"].apply(lambda x: json.loads(x) if isinstance(x, str) and x else {})

st.subheader(f"Run Overview: {selected_run['name']}")
metric_1, metric_2, metric_3, metric_4 = st.columns(4)
metric_1.metric("Samples", int(selected_run["total_rows"]))
metric_2.metric("Detected Events", len(events_df))
metric_3.metric("Open Failures", int((failures_df["status"] == "open").sum()) if not failures_df.empty else 0)
metric_4.metric("Resolved", int((failures_df["status"] == "resolved").sum()) if not failures_df.empty else 0)

if policy_assessment_data is not None:
    st.metric("Policy Verdict", str(policy_assessment_data.get("verdict", "unknown")).upper())
    st.caption(
        f"Policy snapshot v{policy_assessment_data.get('version', 'n/a')} "
        f"evaluated at {policy_assessment_data.get('evaluated_at', 'unknown')}"
    )

if not samples_df.empty:
    chart_df = samples_df[["ts", "speed_mps", "acceleration_mps2", "steering_deg"]].copy()
    chart_long = chart_df.melt(id_vars=["ts"], var_name="signal", value_name="value")
    fig = px.line(chart_long, x="ts", y="value", color="signal", title="Vehicle Signals Over Time")

    if not events_df.empty:
        for _, event in events_df.iterrows():
            color = "red" if event["severity"] in {"high", "critical"} else "orange"
            fig.add_vline(x=event["ts"], line_dash="dot", line_color=color, opacity=0.4)

    st.plotly_chart(fig, use_container_width=True)

st.subheader("Detected Scenario Events")
if events_df.empty:
    st.info("No events detected for this run.")
else:
    st.dataframe(
        events_df[["id", "event_type", "ts", "severity", "details_json"]],
        use_container_width=True,
        hide_index=True,
    )

    st.markdown("Flag events as failures for engineering triage.")
    selectable_event_ids = events_df["id"].tolist()
    event_to_flag = st.multiselect("Choose Event IDs", options=selectable_event_ids)
    failure_reason = st.text_input("Failure reason", value="Safety threshold exceeded")
    notes = st.text_area("Failure notes (optional)")
    if st.button("Create Failure Flags"):
        if not event_to_flag:
            st.warning("Select at least one event to flag.")
        else:
            for event_id in event_to_flag:
                create_failure(int(selected_run_id), reason=failure_reason, event_id=int(event_id), notes=notes or None)
            st.success(f"Created {len(event_to_flag)} failure flag(s).")

    st.subheader("Event Replay Window")
    replay_event_id = st.selectbox("Replay around Event ID", options=events_df["id"].tolist())
    replay_window_seconds = st.slider("Window (seconds)", min_value=0.5, max_value=10.0, value=3.0, step=0.5)

    replay_event = events_df.loc[events_df["id"] == replay_event_id].iloc[0]
    replay_ts = pd.to_datetime(replay_event["ts"])
    replay_samples = build_event_window(samples_df, replay_ts, replay_window_seconds)

    if replay_samples.empty:
        st.info("No samples available in the selected replay window.")
    else:
        replay_chart_df = replay_samples[["ts", "speed_mps", "acceleration_mps2", "steering_deg"]].copy()
        replay_long = replay_chart_df.melt(id_vars=["ts"], var_name="signal", value_name="value")
        replay_fig = px.line(
            replay_long,
            x="ts",
            y="value",
            color="signal",
            title=f"Replay around event {replay_event_id}: {replay_event['event_type']}",
        )
        replay_fig.add_vline(x=replay_ts, line_dash="dash", line_color="red", opacity=0.6)
        st.plotly_chart(replay_fig, use_container_width=True)
        st.caption(
            f"Event time: {replay_ts} | Severity: {replay_event['severity']} | Type: {replay_event['event_type']}"
        )

st.subheader("Failure Tracker")
if failures_df.empty:
    st.info("No failures flagged for this run.")
else:
    st.dataframe(failures_df, use_container_width=True, hide_index=True)
    unresolved = failures_df[failures_df["status"] == "open"]
    if not unresolved.empty:
        resolve_target = st.selectbox("Resolve failure", options=unresolved["id"].tolist())
        resolve_note = st.text_input("Resolution note")
        if st.button("Mark as Resolved"):
            update_failure_status(int(resolve_target), "resolved", notes=resolve_note or None)
            st.success(f"Failure {resolve_target} marked as resolved.")

st.subheader("Export Engineering Report")
summary = build_run_summary(selected_run, events_df, failures_df, policy_assessment=policy_assessment_data)

if policy_assessment_data is not None:
    st.subheader("Policy Assessment")
    rules_df = pd.DataFrame(policy_assessment_data.get("rules", []))
    if not rules_df.empty:
        st.dataframe(rules_df, use_container_width=True, hide_index=True)

    st.subheader("Policy Snapshot History")
    if policy_history:
        history_rows: list[dict[str, object]] = []
        for snapshot in policy_history:
            history_rows.append(
                {
                    "snapshot_id": snapshot["snapshot_id"],
                    "version": snapshot["version"],
                    "verdict": snapshot["verdict"],
                    "evaluated_at": snapshot["evaluated_at"],
                }
            )
        st.dataframe(pd.DataFrame(history_rows), use_container_width=True, hide_index=True)
    else:
        st.info("No policy snapshots recorded for this run.")

report_col_1, report_col_2 = st.columns(2)
with report_col_1:
    st.code(summary_to_markdown(summary), language="markdown")

with report_col_2:
    report_zip = export_bundle(summary, events_df, failures_df)
    st.download_button(
        label="Download Report Bundle (.zip)",
        data=report_zip,
        file_name=f"run-{selected_run_id}-report.zip",
        mime="application/zip",
    )

st.subheader("Run Maintenance")
confirm_delete = st.checkbox("I understand deleting a run also removes its events and failures")
if st.button("Delete Selected Run", type="secondary"):
    if not st.session_state.reviewer_mode:
        st.warning("Enable Reviewer mode to delete runs.")
    elif not confirm_delete:
        st.warning("Tick the confirmation checkbox before deleting a run.")
    else:
        deleted = delete_run(int(selected_run_id))
        if deleted:
            st.success(f"Run {selected_run_id} deleted.")
            st.rerun()
        else:
            st.error(f"Run {selected_run_id} no longer exists.")
