"""
gateway/policy.py
=================
Tool definitions and the default-deny allow-list.

A Model Context Protocol server can expose many tools, and the agent in front
of it will happily call any of them if nothing stops it. The policy here is the
opposite stance: nothing is callable unless a named principal has been granted
it explicitly. This is the structural defence against the confused deputy
problem. The gateway acts only on the authority of the caller in front of it,
never on the broad ambient authority of whatever the underlying server happens
to expose.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ToolSpec:
    """A tool the gateway is willing to broker, with the schema it must satisfy."""

    name: str
    input_schema: dict
    description: str = ""
    destructive: bool = False  # marks high-risk tools for tighter review


@dataclass
class Policy:
    """Default deny. A principal may call only the tools it has been granted."""

    _grants: dict[str, set[str]] = field(default_factory=dict)

    def grant(self, principal: str, *tools: str) -> None:
        self._grants.setdefault(principal, set()).update(tools)

    def allows(self, principal: str, tool: str) -> bool:
        return tool in self._grants.get(principal, set())

    def granted(self, principal: str) -> set[str]:
        return set(self._grants.get(principal, set()))
