from collections.abc import Sequence

from preauth.domain.errors import IntegrityViolationError
from preauth.rules.model import EvaluationOutcome, Rule, RuleContext


class RuleSetEngine:
    """Evaluates every rule in a versioned ruleset independently and returns all results.

    Rules never short-circuit each other: the reviewer sees the full picture. Exceptions raised by a rule are
    not converted into outcomes; they propagate so that no recommendation is produced from a partial evaluation.
    """

    def __init__(self, name: str, version: str, rules: Sequence[Rule]):
        ids = [r.rule_id for r in rules]
        if len(ids) != len(set(ids)):
            raise ValueError(f"Duplicate rule ids in ruleset {name}@{version}: {ids}")
        self.name = name
        self.version = version
        self._rules = tuple(rules)

    @property
    def rules(self) -> tuple[Rule, ...]:
        return self._rules

    def evaluate(self, ctx: RuleContext) -> EvaluationOutcome:
        results = []
        for rule in self._rules:
            result = rule.evaluate(ctx)
            if result.rule_id != rule.rule_id or result.rule_version != rule.version:
                raise IntegrityViolationError(
                    "Rule returned a result attributed to a different rule",
                    details={
                        "rule_id": rule.rule_id,
                        "rule_version": rule.version,
                        "result_rule_id": result.rule_id,
                        "result_rule_version": result.rule_version,
                    },
                )
            results.append(result)
        return EvaluationOutcome(engine_name=self.name, engine_version=self.version, results=tuple(results))
