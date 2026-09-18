"""Deterministic automated-assurance evaluation for release gates."""

from dataclasses import dataclass
from enum import StrEnum


class TestCategory(StrEnum):
    __test__ = False

    POSITIVE = "POSITIVE"
    NEGATIVE = "NEGATIVE"
    REGRESSION = "REGRESSION"
    SECURITY = "SECURITY"
    RECOVERY = "RECOVERY"


@dataclass(frozen=True, slots=True)
class ExpectedBehavior:
    status: str
    error_code: str | None = None
    reason_contains: str | None = None

    def __post_init__(self) -> None:
        if not self.status:
            raise ValueError("expected status is required")


@dataclass(frozen=True, slots=True)
class ObservedBehavior:
    status: str
    error_code: str | None = None
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class AssuranceCheck:
    test_id: str
    category: TestCategory
    expected: ExpectedBehavior
    observed: ObservedBehavior
    passed: bool
    mismatches: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class AutomatedAssuranceReport:
    checks: tuple[AssuranceCheck, ...]
    required_categories: frozenset[TestCategory]

    @property
    def passed(self) -> bool:
        observed_categories = {check.category for check in self.checks}
        return (
            bool(self.checks)
            and self.required_categories.issubset(observed_categories)
            and all(check.passed for check in self.checks)
        )


class AssuranceEvaluator:
    """Compare observations to expectations; no category receives an automatic pass."""

    def evaluate(
        self,
        *,
        test_id: str,
        category: TestCategory,
        expected: ExpectedBehavior,
        observed: ObservedBehavior,
    ) -> AssuranceCheck:
        if category is TestCategory.NEGATIVE and expected.status in {
            "SUCCESS",
            "SUCCEEDED",
            "PASS",
            "PASSED",
        }:
            raise ValueError("negative tests must expect a denied or failed behavior")

        mismatches: list[str] = []
        if observed.status != expected.status:
            mismatches.append(f"status expected {expected.status}, observed {observed.status}")
        if expected.error_code is not None and observed.error_code != expected.error_code:
            mismatches.append(
                f"error_code expected {expected.error_code}, observed {observed.error_code}"
            )
        if expected.reason_contains is not None and (
            observed.reason is None or expected.reason_contains not in observed.reason
        ):
            mismatches.append("observed reason does not contain the expected text")
        return AssuranceCheck(
            test_id=test_id,
            category=category,
            expected=expected,
            observed=observed,
            passed=not mismatches,
            mismatches=tuple(mismatches),
        )
