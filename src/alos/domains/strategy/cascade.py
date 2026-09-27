"""Deterministic, expression-free strategy cascade rule engine."""

from __future__ import annotations

from decimal import ROUND_CEILING, Decimal

from alos.domains.strategy.models import CalculationTrace, CascadeRule, CascadeStatus


class CascadeEngine:
    def calculate(
        self,
        rule: CascadeRule,
        *,
        inputs: dict[str, Decimal | None],
    ) -> CalculationTrace:
        if rule.rule_type.value == "DIRECT":
            return self._direct(rule, inputs)
        if rule.rule_type.value == "SPLIT_FIXED":
            return self._split_fixed(rule, inputs)
        if rule.rule_type.value == "SPLIT_PERCENT":
            return self._split_percent(rule, inputs)
        if rule.rule_type.value == "SUM_ROLLUP":
            return self._sum_rollup(rule, inputs)
        if rule.rule_type.value == "RATIO_MULTIPLY":
            return self._ratio_multiply(rule, inputs)
        if rule.rule_type.value == "RATIO_DIVIDE_CEIL":
            return self._ratio_divide_ceil(rule, inputs)
        if rule.rule_type.value == "LIMIT_CHECK":
            return self._limit_check(rule, inputs)
        raise ValueError(f"Unsupported cascade rule: {rule.rule_type.value}")

    @staticmethod
    def _trace(
        rule: CascadeRule,
        inputs: dict[str, Decimal | None],
        output: Decimal | None,
        status: CascadeStatus,
        *,
        rounding: str | None = None,
        message: str | None = None,
    ) -> CalculationTrace:
        return CalculationTrace(
            rule_id=rule.rule_id,
            rule_type=rule.rule_type,
            output_target_id=rule.output_target_id,
            inputs={key: None if value is None else str(value) for key, value in inputs.items()},
            rounding_mode=rounding,
            output=None if output is None else str(output),
            status=status,
            message=message,
        )

    def _direct(self, rule: CascadeRule, inputs: dict[str, Decimal | None]) -> CalculationTrace:
        value = inputs.get("value")
        status = CascadeStatus.VALID if value is not None else CascadeStatus.INCOMPLETE
        return self._trace(
            rule, inputs, value, status, message=None if value is not None else "missing input"
        )

    def _split_fixed(
        self, rule: CascadeRule, inputs: dict[str, Decimal | None]
    ) -> CalculationTrace:
        values = [value for key, value in inputs.items() if key.startswith("child:")]
        if not values or any(value is None for value in values):
            return self._trace(
                rule, inputs, None, CascadeStatus.INCOMPLETE, message="missing child"
            )
        output = sum((value for value in values if value is not None), Decimal(0))
        expected = inputs.get("parent")
        if rule.parameters.get("require_rollup") and expected is not None and output != expected:
            return self._trace(
                rule, inputs, output, CascadeStatus.INVALID, message="fixed split does not roll up"
            )
        return self._trace(rule, inputs, output, CascadeStatus.VALID)

    def _split_percent(
        self, rule: CascadeRule, inputs: dict[str, Decimal | None]
    ) -> CalculationTrace:
        parent, ratio = inputs.get("parent"), inputs.get("ratio")
        if rule.parameters.get("require_full_allocation"):
            shares = [value for key, value in inputs.items() if key.startswith("share:")]
            if not shares or any(value is None for value in shares):
                return self._trace(
                    rule,
                    inputs,
                    None,
                    CascadeStatus.INCOMPLETE,
                    message="full allocation shares are incomplete",
                )
            if any(self._ratio_status(value) is not None for value in shares) or sum(
                (value for value in shares if value is not None), Decimal(0)
            ) != Decimal(1):
                return self._trace(
                    rule,
                    inputs,
                    None,
                    CascadeStatus.INVALID,
                    message="full allocation shares must total exactly 1",
                )
        invalid = self._ratio_status(ratio)
        if parent is None or ratio is None:
            return self._trace(
                rule, inputs, None, CascadeStatus.INCOMPLETE, message="missing parent or ratio"
            )
        if invalid is not None:
            return self._trace(
                rule, inputs, None, invalid, message="ratio must be greater than 0 and at most 1"
            )
        return self._trace(rule, inputs, parent * ratio, CascadeStatus.VALID)

    def _sum_rollup(self, rule: CascadeRule, inputs: dict[str, Decimal | None]) -> CalculationTrace:
        if not inputs or any(value is None for value in inputs.values()):
            return self._trace(
                rule, inputs, None, CascadeStatus.INCOMPLETE, message="missing child"
            )
        return self._trace(
            rule,
            inputs,
            sum((v for v in inputs.values() if v is not None), Decimal(0)),
            CascadeStatus.VALID,
        )

    def _ratio_multiply(
        self, rule: CascadeRule, inputs: dict[str, Decimal | None]
    ) -> CalculationTrace:
        value, ratio = inputs.get("input"), inputs.get("ratio")
        if value is None or ratio is None:
            return self._trace(
                rule, inputs, None, CascadeStatus.INCOMPLETE, message="missing input or ratio"
            )
        invalid = self._ratio_status(ratio)
        if invalid is not None:
            return self._trace(
                rule, inputs, None, invalid, message="ratio must be greater than 0 and at most 1"
            )
        return self._trace(rule, inputs, value * ratio, CascadeStatus.VALID)

    def _ratio_divide_ceil(
        self, rule: CascadeRule, inputs: dict[str, Decimal | None]
    ) -> CalculationTrace:
        value, ratio = inputs.get("input"), inputs.get("ratio")
        if value is None or ratio is None:
            return self._trace(
                rule, inputs, None, CascadeStatus.INCOMPLETE, message="missing input or ratio"
            )
        invalid = self._ratio_status(ratio)
        if invalid is not None:
            return self._trace(
                rule, inputs, None, invalid, message="ratio must be greater than 0 and at most 1"
            )
        output = (value / ratio).to_integral_value(rounding=ROUND_CEILING)
        return self._trace(rule, inputs, output, CascadeStatus.VALID, rounding="CEILING")

    def _limit_check(
        self, rule: CascadeRule, inputs: dict[str, Decimal | None]
    ) -> CalculationTrace:
        required, available = inputs.get("required"), inputs.get("available")
        if required is None or available is None:
            return self._trace(
                rule, inputs, None, CascadeStatus.INCOMPLETE, message="missing capacity source"
            )
        status = CascadeStatus.VALID if required <= available else CascadeStatus.INVALID
        return self._trace(
            rule,
            inputs,
            required,
            status,
            message=None if status is CascadeStatus.VALID else "limit exceeded",
        )

    @staticmethod
    def _ratio_status(ratio: Decimal | None) -> CascadeStatus | None:
        if ratio is None:
            return CascadeStatus.INCOMPLETE
        if ratio <= 0 or ratio > 1:
            return CascadeStatus.INVALID
        return None
