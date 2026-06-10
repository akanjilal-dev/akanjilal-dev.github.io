"""
finops/focus.py
===============
Ingest billing data in the **FOCUS** format -- the FinOps Open Cost and Usage
Specification, the open standard that normalises every cloud's billing export
into one schema. Working against FOCUS instead of a vendor's native CUR/CSV is
the whole point: the analytics downstream don't care whether the bill came from
AWS, Azure, GCP, or an LLM provider.

This module is dependency-free (stdlib `csv` only). It loads a FOCUS CSV into
typed `BillingRecord`s, coercing the columns we actually analyse and parsing the
`Tags` column (JSON object) into a dict. Unknown extra columns are ignored, so a
fuller real-world export still loads.
"""

from __future__ import annotations

import csv
import json
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from datetime import date, datetime

# FOCUS columns we rely on. A real export carries dozens more; these are enough
# to answer "where is spend going, and what's anomalous?".
REQUIRED_COLUMNS = (
    "ChargePeriodStart",
    "ServiceName",
    "ServiceCategory",
    "ChargeCategory",
    "BilledCost",
    "EffectiveCost",
)


@dataclass
class BillingRecord:
    charge_period_start: date
    service_name: str
    service_category: str
    charge_category: str  # Usage | Purchase | Tax | Credit | Adjustment
    billed_cost: float
    effective_cost: float
    provider_name: str = ""
    region_id: str = ""
    resource_id: str = ""
    resource_type: str = ""
    charge_description: str = ""
    consumed_quantity: float = 0.0
    consumed_unit: str = ""
    tags: dict[str, str] = field(default_factory=dict)

    @property
    def day(self) -> date:
        return self.charge_period_start

    def tag(self, key: str, default: str = "") -> str:
        return self.tags.get(key, default)


def _to_float(value: str) -> float:
    value = (value or "").strip()
    if not value:
        return 0.0
    return float(value)


def _to_date(value: str) -> date:
    value = (value or "").strip()
    # FOCUS timestamps are ISO 8601; accept a plain date too.
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
    except ValueError:
        return date.fromisoformat(value[:10])


def _parse_tags(value: str) -> dict[str, str]:
    value = (value or "").strip()
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return {}
    if not isinstance(parsed, dict):
        return {}
    return {str(k): str(v) for k, v in parsed.items()}


def _row_to_record(row: dict[str, str]) -> BillingRecord:
    return BillingRecord(
        charge_period_start=_to_date(row.get("ChargePeriodStart", "")),
        service_name=(row.get("ServiceName") or "").strip(),
        service_category=(row.get("ServiceCategory") or "").strip(),
        charge_category=(row.get("ChargeCategory") or "Usage").strip(),
        billed_cost=_to_float(row.get("BilledCost", "")),
        effective_cost=_to_float(row.get("EffectiveCost", "")),
        provider_name=(row.get("ProviderName") or "").strip(),
        region_id=(row.get("RegionId") or "").strip(),
        resource_id=(row.get("ResourceId") or "").strip(),
        resource_type=(row.get("ResourceType") or "").strip(),
        charge_description=(row.get("ChargeDescription") or "").strip(),
        consumed_quantity=_to_float(row.get("ConsumedQuantity", "")),
        consumed_unit=(row.get("ConsumedUnit") or "").strip(),
        tags=_parse_tags(row.get("Tags", "")),
    )


def load_focus(path: str) -> list[BillingRecord]:
    """Load a FOCUS-format CSV file into typed billing records."""
    with open(path, newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        missing = [c for c in REQUIRED_COLUMNS if c not in (reader.fieldnames or [])]
        if missing:
            raise ValueError(
                f"{path} is not a valid FOCUS export -- missing columns: {', '.join(missing)}"
            )
        return [_row_to_record(row) for row in reader]


def parse_focus(rows: Iterable[dict[str, str]]) -> Iterator[BillingRecord]:
    """Parse already-read FOCUS rows (dicts) into records -- handy for tests."""
    for row in rows:
        yield _row_to_record(row)
