"""
finops/anomalies.py
===================
The "what's wrong?" layer. Three detectors that catch the AI-spend failure modes
that actually burn money:

  * idle_gpu        -- expensive accelerators provisioned and billed while doing
                       ~no useful work (the classic cloud-AI waste).
  * inference_spikes-- a day where a service's spend jumps far above its own
                       recent baseline (a runaway loop, a price change, an abuse).
  * cost_spikes     -- the same idea applied to total daily spend.

The detectors are statistical and explainable -- median + MAD, not a black box --
so every finding comes with the numbers behind it. A finding you can't explain is
a finding nobody will action.
"""

from __future__ import annotations

import statistics
from collections.abc import Iterable
from dataclasses import dataclass

from finops.analytics import _metric, daily_cost, is_ai
from finops.focus import BillingRecord

# A GPU is "idle" if it is billed above this much over the period but its tagged
# utilization is below this percentage. Utilization rides on a `utilization_pct`
# tag here; in production it would come from CloudWatch / DCGM metrics.
IDLE_GPU_MIN_COST = 50.0
IDLE_GPU_MAX_UTIL_PCT = 10.0
GPU_HINTS = ("gpu", "p4d", "p5", "a100", "h100", "g5", "accelerator")


@dataclass
class Finding:
    kind: str
    subject: str
    detail: str
    impact_usd: float

    def __str__(self) -> str:
        return f"[{self.kind}] {self.subject} — {self.detail} (≈${self.impact_usd:,.2f})"


def _looks_like_gpu(record: BillingRecord) -> bool:
    haystack = f"{record.resource_type} {record.resource_id} {record.charge_description}".lower()
    return any(hint in haystack for hint in GPU_HINTS)


def detect_idle_gpu(
    records: Iterable[BillingRecord], metric: str = "EffectiveCost"
) -> list[Finding]:
    """Flag GPU resources that cost real money while tagged as under-utilised."""
    cost_by_res: dict[str, float] = {}
    util_by_res: dict[str, float] = {}
    label: dict[str, str] = {}

    for r in records:
        if not _looks_like_gpu(r):
            continue
        rid = r.resource_id or "(no resource id)"
        cost_by_res[rid] = cost_by_res.get(rid, 0.0) + _metric(r, metric)
        label[rid] = r.resource_type or r.service_name
        util = r.tag("utilization_pct")
        if util:
            try:
                # last/most-recent utilization wins; fine for a tag-based signal
                util_by_res[rid] = float(util)
            except ValueError:
                pass

    findings: list[Finding] = []
    for rid, cost in cost_by_res.items():
        util = util_by_res.get(rid)
        if cost >= IDLE_GPU_MIN_COST and util is not None and util <= IDLE_GPU_MAX_UTIL_PCT:
            findings.append(
                Finding(
                    kind="IDLE_GPU",
                    subject=rid,
                    detail=f"{label.get(rid, 'GPU')} at {util:.0f}% utilization billed ${cost:,.2f}",
                    impact_usd=round(cost, 2),
                )
            )
    return sorted(findings, key=lambda f: f.impact_usd, reverse=True)


def _spikes_in_series(
    series: dict[str, float], sensitivity: float
) -> list[tuple[str, float, float]]:
    """Return (day, cost, baseline) for days whose cost is a robust outlier.

    Uses the median and median-absolute-deviation of the *other* days, so a
    single huge day doesn't hide itself by inflating its own baseline.
    """
    days = list(series)
    spikes: list[tuple[str, float, float]] = []
    if len(days) < 4:
        return spikes
    for i, day in enumerate(days):
        others = [series[d] for j, d in enumerate(days) if j != i]
        baseline = statistics.median(others)
        mad = statistics.median([abs(x - baseline) for x in others]) or 1.0
        if series[day] > baseline + sensitivity * mad and series[day] > baseline * 1.5:
            spikes.append((day, series[day], baseline))
    return spikes


def detect_inference_spikes(
    records: Iterable[BillingRecord], sensitivity: float = 4.0, metric: str = "EffectiveCost"
) -> list[Finding]:
    """Flag days where AI/ML spend jumps far above its own recent baseline."""
    ai = [r for r in records if is_ai(r)]
    series = daily_cost(ai, metric)
    findings = []
    for day, cost, baseline in _spikes_in_series(series, sensitivity):
        findings.append(
            Finding(
                kind="INFERENCE_SPIKE",
                subject=day,
                detail=f"AI spend ${cost:,.2f} vs ${baseline:,.2f} baseline ({cost / baseline:.1f}x)",
                impact_usd=round(cost - baseline, 2),
            )
        )
    return findings


def detect_cost_spikes(
    records: Iterable[BillingRecord], sensitivity: float = 4.0, metric: str = "EffectiveCost"
) -> list[Finding]:
    """Flag days where *total* spend jumps far above baseline."""
    series = daily_cost(list(records), metric)
    findings = []
    for day, cost, baseline in _spikes_in_series(series, sensitivity):
        findings.append(
            Finding(
                kind="COST_SPIKE",
                subject=day,
                detail=f"total spend ${cost:,.2f} vs ${baseline:,.2f} baseline ({cost / baseline:.1f}x)",
                impact_usd=round(cost - baseline, 2),
            )
        )
    return findings


def scan(records: Iterable[BillingRecord]) -> list[Finding]:
    """Run every detector and return all findings, biggest impact first."""
    records = list(records)
    findings = (
        detect_idle_gpu(records)
        + detect_inference_spikes(records)
        + detect_cost_spikes(records)
    )
    return sorted(findings, key=lambda f: f.impact_usd, reverse=True)
