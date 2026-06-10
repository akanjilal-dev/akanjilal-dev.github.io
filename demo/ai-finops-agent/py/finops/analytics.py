"""
finops/analytics.py
===================
Pure, deterministic analytics over FOCUS billing records. No LLM, no network,
no surprises -- every number here is auditable arithmetic, which is exactly what
you want when the output is a spend figure someone will act on.

`EffectiveCost` is the default metric (what you actually pay after discounts and
commitments), per FinOps convention. Credits and other non-usage charges are
included in totals unless filtered out by the caller.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable

from finops.focus import BillingRecord

# FOCUS ServiceCategory / ConsumedUnit values that indicate AI/ML spend and
# "outcomes" (billable units of useful work) respectively.
AI_SERVICE_CATEGORIES = {"AI and Machine Learning"}
OUTCOME_UNITS = {"Inferences", "Requests", "Tokens", "Predictions"}

_DIMENSION_ATTR = {
    "service": "service_name",
    "service_category": "service_category",
    "category": "service_category",
    "provider": "provider_name",
    "region": "region_id",
    "resource": "resource_id",
    "resource_type": "resource_type",
}


def _metric(record: BillingRecord, metric: str) -> float:
    return getattr(record, "effective_cost" if metric == "EffectiveCost" else "billed_cost")


def total_cost(records: Iterable[BillingRecord], metric: str = "EffectiveCost") -> float:
    return round(sum(_metric(r, metric) for r in records), 2)


def is_ai(record: BillingRecord) -> bool:
    return record.service_category in AI_SERVICE_CATEGORIES


def filter_ai(records: Iterable[BillingRecord]) -> list[BillingRecord]:
    return [r for r in records if is_ai(r)]


def cost_by(
    records: Iterable[BillingRecord],
    dimension: str,
    metric: str = "EffectiveCost",
) -> dict[str, float]:
    """Total cost grouped by a dimension (service, region, provider, ...), or a tag.

    `dimension` may be a known field name or `tag:<key>` to group by a tag.
    Returns a dict ordered high-to-low by cost.
    """
    totals: dict[str, float] = defaultdict(float)
    for r in records:
        if dimension.startswith("tag:"):
            key = r.tag(dimension[4:], "(untagged)")
        else:
            attr = _DIMENSION_ATTR.get(dimension)
            if attr is None:
                raise ValueError(f"unknown dimension {dimension!r}")
            key = getattr(r, attr) or "(none)"
        totals[key] += _metric(r, metric)
    return dict(sorted(((k, round(v, 2)) for k, v in totals.items()), key=lambda kv: kv[1], reverse=True))


def daily_cost(
    records: Iterable[BillingRecord],
    metric: str = "EffectiveCost",
) -> dict[str, float]:
    """Cost per day, ordered chronologically (ISO date strings as keys)."""
    totals: dict[str, float] = defaultdict(float)
    for r in records:
        totals[r.day.isoformat()] += _metric(r, metric)
    return {k: round(totals[k], 2) for k in sorted(totals)}


def top_resources(
    records: Iterable[BillingRecord],
    n: int = 5,
    metric: str = "EffectiveCost",
) -> list[tuple[str, float]]:
    totals: dict[str, float] = defaultdict(float)
    for r in records:
        totals[r.resource_id or "(no resource id)"] += _metric(r, metric)
    ranked = sorted(((k, round(v, 2)) for k, v in totals.items()), key=lambda kv: kv[1], reverse=True)
    return ranked[:n]


def cost_per_outcome(
    records: Iterable[BillingRecord],
    metric: str = "EffectiveCost",
) -> dict[str, float]:
    """Cost efficiency: dollars per 1,000 billable outcomes (inferences/requests).

    Returns total cost, total outcomes, and cost per 1K outcomes. This is the
    metric that turns "we spent $X on AI" into "each prediction cost us $Y" --
    the number a CFO can actually reason about.
    """
    cost = 0.0
    outcomes = 0.0
    for r in records:
        if r.consumed_unit in OUTCOME_UNITS:
            cost += _metric(r, metric)
            outcomes += r.consumed_quantity
    per_1k = round((cost / outcomes) * 1000, 4) if outcomes else 0.0
    return {
        "cost": round(cost, 2),
        "outcomes": round(outcomes, 0),
        "cost_per_1k_outcomes": per_1k,
    }
