"""
harness/runner.py
=================
Cases, the runner, and a report you can gate a deploy on.

A `Case` is one input plus the checks that must hold for it. `run_suite` runs an
agent through every case and returns a `Report`. The report's `ok` property is
the whole point: wire `assert run_suite(agent, SUITE).ok` into CI and a
behavioural regression -- a newly leaked secret, a tool called that should not
be, a cost blowout -- fails the build the same way a unit test would.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from harness.checks import Check, CheckOutcome
from harness.types import AgentResult

Agent = Callable[[str], AgentResult]


@dataclass
class Case:
    name: str
    input: str
    checks: list[Check]
    tags: tuple = ()


@dataclass
class CaseResult:
    case: Case
    result: AgentResult
    outcomes: list[CheckOutcome]

    @property
    def passed(self) -> bool:
        return self.result.error is None and all(o.passed for o in self.outcomes)


@dataclass
class Report:
    results: list[CaseResult]

    @property
    def total(self) -> int:
        return len(self.results)

    @property
    def passed_count(self) -> int:
        return sum(1 for r in self.results if r.passed)

    @property
    def ok(self) -> bool:
        return all(r.passed for r in self.results)

    def summary(self, title: str = "suite") -> str:
        lines = [f"Suite: {title}"]
        for r in self.results:
            lines.append(f"  {'PASS' if r.passed else 'FAIL'}  {r.case.name}")
            if r.result.error:
                lines.append(f"          error: {r.result.error}")
            for o in r.outcomes:
                if not o.passed:
                    lines.append(f"          - {o.label}: {o.detail}")
        tail = "" if self.ok else "   (this would block the deploy)"
        lines.append(f"  {self.passed_count} of {self.total} cases passed{tail}")
        return "\n".join(lines)


def run_case(agent: Agent, case: Case) -> CaseResult:
    try:
        result = agent(case.input)
    except Exception as exc:  # a crashing agent is a failing case, not a crashing suite
        result = AgentResult(error=str(exc))
    outcomes = [c.run(result) for c in case.checks]
    return CaseResult(case, result, outcomes)


def run_suite(agent: Agent, cases: Sequence[Case]) -> Report:
    return Report([run_case(agent, c) for c in cases])
