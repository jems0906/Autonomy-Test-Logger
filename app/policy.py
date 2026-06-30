from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

import pandas as pd


@dataclass(frozen=True)
class PolicyConfig:
    max_cut_in_events: int = 0
    max_speeding_events: int = 0
    max_stop_sign_violations: int = 0
    max_braking_events: int = 2
    max_aggressive_steering_events: int = 3
    max_hard_accel_events: int = 3


@dataclass(frozen=True)
class PolicyRuleResult:
    rule_id: str
    event_type: str
    observed: int
    max_allowed: int
    passed: bool


@dataclass(frozen=True)
class PolicyAssessment:
    verdict: str
    rules: list[PolicyRuleResult]
    counts: dict[str, int]

    def to_dict(self) -> dict[str, Any]:
        return {
            "verdict": self.verdict,
            "rules": [asdict(r) for r in self.rules],
            "counts": self.counts,
        }


def evaluate_policy(events_df: pd.DataFrame, config: PolicyConfig | None = None) -> PolicyAssessment:
    config = config or PolicyConfig()
    counts = events_df["event_type"].value_counts().to_dict() if not events_df.empty else {}

    rules = [
        evaluate_rule("cut_in_zero_tolerance", "cut_in", counts, config.max_cut_in_events),
        evaluate_rule(
            "speeding_zero_tolerance",
            "speed_threshold_exceeded",
            counts,
            config.max_speeding_events,
        ),
        evaluate_rule(
            "stop_sign_violation_zero_tolerance",
            "stop_sign_violation",
            counts,
            config.max_stop_sign_violations,
        ),
        evaluate_rule("braking_limit", "braking", counts, config.max_braking_events),
        evaluate_rule(
            "aggressive_steering_limit",
            "aggressive_steering",
            counts,
            config.max_aggressive_steering_events,
        ),
        evaluate_rule(
            "hard_acceleration_limit",
            "hard_acceleration",
            counts,
            config.max_hard_accel_events,
        ),
    ]

    verdict = "pass" if all(rule.passed for rule in rules) else "fail"
    return PolicyAssessment(verdict=verdict, rules=rules, counts=counts)


def evaluate_rule(rule_id: str, event_type: str, counts: dict[str, int], max_allowed: int) -> PolicyRuleResult:
    observed = int(counts.get(event_type, 0))
    passed = observed <= max_allowed
    return PolicyRuleResult(
        rule_id=rule_id,
        event_type=event_type,
        observed=observed,
        max_allowed=max_allowed,
        passed=passed,
    )


def build_policy_failures(assessment: PolicyAssessment) -> list[str]:
    failures: list[str] = []
    for rule in assessment.rules:
        if not rule.passed:
            failures.append(
                f"POLICY_FAIL {rule.rule_id}: {rule.event_type} observed={rule.observed} limit={rule.max_allowed}"
            )
    return failures
