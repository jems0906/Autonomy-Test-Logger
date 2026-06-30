import pandas as pd

from app.policy import PolicyConfig, evaluate_policy


def test_policy_fails_on_zero_tolerance_violations() -> None:
    events_df = pd.DataFrame(
        [
            {"event_type": "cut_in", "severity": "high"},
            {"event_type": "speed_threshold_exceeded", "severity": "high"},
        ]
    )

    assessment = evaluate_policy(events_df, PolicyConfig())

    assert assessment.verdict == "fail"
    failed_rules = [rule for rule in assessment.rules if not rule.passed]
    assert len(failed_rules) >= 2


def test_policy_passes_when_limits_are_relaxed() -> None:
    events_df = pd.DataFrame(
        [
            {"event_type": "cut_in", "severity": "high"},
            {"event_type": "speed_threshold_exceeded", "severity": "high"},
        ]
    )

    config = PolicyConfig(max_cut_in_events=2, max_speeding_events=2)
    assessment = evaluate_policy(events_df, config)

    assert assessment.verdict == "pass"
