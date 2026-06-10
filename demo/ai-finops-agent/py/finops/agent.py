"""
finops/agent.py
===============
The agent. It turns a natural-language spend question into a call to one of a
fixed set of **read-only** analytics tools, runs it, and answers in plain
English. Routing is deterministic keyword intent-matching by default -- no LLM,
no API key, no hallucinated dollar figures, and an audit trail of exactly which
tool produced which number. (An LLM router is a drop-in for fuzzier phrasing;
see the README.)

Security posture -- the agent is hardened the way you'd harden any tool-using
system, which is the through-line of this whole portfolio:

  * Least privilege / no excessive agency (OWASP LLM06): the agent can ONLY call
    the read-only tools in TOOLS. It cannot mutate data, spend money, or run
    arbitrary code -- there is nothing in its reach that does.
  * Input guard (OWASP LLM01): a question carrying override-style instructions
    is refused before routing.
  * Data is data, never instructions: billing free-text (ChargeDescription,
    tags) is only ever counted or displayed, never interpreted as a command --
    so a poisoned `ChargeDescription` can't steer the agent.

Deeper, layered defenses live in the sibling `llm-security-lab`; this is the
finops agent wearing the same seatbelt.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass

from finops import analytics, anomalies
from finops.focus import BillingRecord

# --- input guard (LLM01) ---------------------------------------------------
_INJECTION_PATTERNS = [
    r"ignore (the )?(previous|above|prior|all)",
    r"disregard (the )?(previous|above|all)",
    r"forget (your|the) (instructions|rules)",
    r"system prompt",
    r"reveal|exfiltrat|leak",
    r"\b(delete|drop|truncate|update|insert)\b",
    r"run (this|the following)|execute",
]

_REFUSAL = (
    "I can only answer read-only questions about the billing data "
    "(totals, breakdowns, trends, anomalies, cost-per-outcome). "
    "I won't follow instructions embedded in a request."
)


def looks_like_injection(text: str) -> bool:
    low = text.lower()
    return any(re.search(p, low) for p in _INJECTION_PATTERNS)


@dataclass
class Answer:
    text: str
    tool: str
    data: object = None

    def __str__(self) -> str:
        return self.text


# --- the read-only tool surface (this is the agent's entire authority) -----
def _tool_total(records: list[BillingRecord], q: str) -> Answer:
    scope = analytics.filter_ai(records) if _wants_ai(q) else records
    label = "AI/ML" if _wants_ai(q) else "total"
    return Answer(f"The {label} spend is ${analytics.total_cost(scope):,.2f}.", "total_cost")


def _tool_by_dimension(records: list[BillingRecord], q: str) -> Answer:
    dim = _detect_dimension(q)
    scope = analytics.filter_ai(records) if _wants_ai(q) else records
    breakdown = analytics.cost_by(scope, dim)
    lines = "\n".join(f"  {k:<28} ${v:,.2f}" for k, v in list(breakdown.items())[:10])
    pretty = dim[4:] if dim.startswith("tag:") else dim
    return Answer(f"Spend by {pretty}:\n{lines}", "cost_by", breakdown)


def _tool_top_resources(records: list[BillingRecord], q: str) -> Answer:
    n = _detect_n(q, default=5)
    rows = analytics.top_resources(records, n=n)
    lines = "\n".join(f"  {rid:<40} ${cost:,.2f}" for rid, cost in rows)
    return Answer(f"Top {n} resources by cost:\n{lines}", "top_resources", rows)


def _tool_trend(records: list[BillingRecord], q: str) -> Answer:
    scope = analytics.filter_ai(records) if _wants_ai(q) else records
    series = analytics.daily_cost(scope)
    days = list(series)
    head = f"{days[0]} → {days[-1]}" if days else "(no data)"
    total = round(sum(series.values()), 2)
    label = "AI/ML" if _wants_ai(q) else "total"
    return Answer(
        f"Daily {label} spend over {head}: {len(days)} days, ${total:,.2f} total, "
        f"peak ${max(series.values()):,.2f} on {max(series, key=series.get)}.",
        "daily_cost",
        series,
    )


def _tool_cost_per_outcome(records: list[BillingRecord], q: str) -> Answer:
    cpo = analytics.cost_per_outcome(records)
    if not cpo["outcomes"]:
        return Answer("No outcome-metered usage (inferences/requests) found in the data.", "cost_per_outcome", cpo)
    return Answer(
        f"Cost-per-outcome: ${cpo['cost']:,.2f} across {int(cpo['outcomes']):,} outcomes "
        f"= ${cpo['cost_per_1k_outcomes']:,.4f} per 1,000.",
        "cost_per_outcome",
        cpo,
    )


def _tool_anomalies(records: list[BillingRecord], q: str) -> Answer:
    findings = anomalies.scan(records)
    if not findings:
        return Answer("No spend anomalies detected.", "scan", findings)
    lines = "\n".join(f"  • {f}" for f in findings)
    waste = sum(f.impact_usd for f in findings)
    return Answer(
        f"Found {len(findings)} anomaly(ies), ≈${waste:,.2f} of impact:\n{lines}",
        "scan",
        findings,
    )


# Intent -> tool. Order matters: earlier, more specific intents win ties.
TOOLS: list[tuple[str, Callable[[list[BillingRecord], str], Answer], list[str]]] = [
    ("anomalies", _tool_anomalies,
     ["anomal", "unusual", "spike", "idle", "wasted", "waste", "what's wrong", "whats wrong", "problem"]),
    ("cost_per_outcome", _tool_cost_per_outcome,
     ["per outcome", "per inference", "per request", "per 1000", "per 1k", "unit cost", "efficiency", "cost per"]),
    ("trend", _tool_trend,
     ["trend", "over time", "daily", "per day", "day by day", "history"]),
    ("top_resources", _tool_top_resources,
     ["top", "biggest", "most expensive", "largest", "which resource", "worst"]),
    ("by_dimension", _tool_by_dimension,
     ["by service", "by region", "by team", "by project", "by provider", "by category",
      "breakdown", "break down", "per service", "per team", "split by", "grouped by", "by "]),
    ("total", _tool_total,
     ["total", "how much", "overall", "sum", "altogether", "grand total"]),
]


def _wants_ai(q: str) -> bool:
    return bool(re.search(r"\b(ai|ml|inference|model|llm|bedrock|sagemaker|gpu)\b", q.lower()))


def _detect_n(q: str, default: int) -> int:
    m = re.search(r"\b(\d{1,3})\b", q)
    return int(m.group(1)) if m else default


def _detect_dimension(q: str) -> str:
    low = q.lower()
    for key in ("service_category", "category", "service", "region", "provider", "resource_type", "resource"):
        if key.replace("_", " ") in low or key in low:
            return key
    for tag in ("team", "project", "model", "env", "environment"):
        if tag in low:
            return f"tag:{tag}"
    return "service"  # sensible default


def _score(question: str, keywords: list[str]) -> int:
    low = question.lower()
    return sum(1 for kw in keywords if kw in low)


class FinOpsAgent:
    """A read-only spend agent over FOCUS billing records."""

    def __init__(self, records: list[BillingRecord]):
        self.records = records

    def ask(self, question: str) -> Answer:
        # Input guard first -- refuse anything that looks like an instruction.
        if looks_like_injection(question):
            return Answer(_REFUSAL, "refused")

        best_tool = None
        best_score = 0
        for name, fn, keywords in TOOLS:
            score = _score(question, keywords)
            if score > best_score:
                best_tool, best_score = (name, fn), score

        if best_tool is None:
            # No intent matched -> safest, most useful default: the total.
            return _tool_total(self.records, question)
        return best_tool[1](self.records, question)
