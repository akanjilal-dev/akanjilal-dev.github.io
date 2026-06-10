"""
gateway/audit.py
================
A structured record of every brokered call.

In a regulated setting the audit trail is not an afterthought, it is the
deliverable. Every decision the gateway makes, allow or deny and why, is
recorded here, so that after the fact you can answer the only question that
matters during an incident: who asked for what, and what did we let through.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class AuditRecord:
    principal: str
    tool: str
    decision: str  # "allow" or "deny"
    reason: str
    findings: tuple[str, ...] = ()

    def __str__(self) -> str:
        tail = f"  [{'; '.join(self.findings)}]" if self.findings else ""
        return f"{self.decision.upper():5}  {self.principal:<18} {self.tool:<14} {self.reason}{tail}"


@dataclass
class AuditLog:
    records: list[AuditRecord] = field(default_factory=list)

    def record(self, principal: str, tool: str, decision: str, reason: str, findings=()) -> None:
        self.records.append(
            AuditRecord(principal, tool, decision, reason, tuple(findings))
        )

    def __iter__(self):
        return iter(self.records)

    def __len__(self) -> int:
        return len(self.records)

    def denials(self) -> list[AuditRecord]:
        return [r for r in self.records if r.decision == "deny"]
