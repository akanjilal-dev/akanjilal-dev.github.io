"""
harness/types.py
================
The shape of an agent's behaviour that the harness inspects.

The harness is framework agnostic. It does not care whether the agent under
test is built on LangGraph, CrewAI, the Claude Agent SDK, or hand-rolled. The
only contract is that one call with a string input returns an `AgentResult`
describing what the agent did: its final text, the tools it called with what
arguments, the tokens it spent, and whether it refused. Any real agent can be
adapted to emit this in a few lines.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ToolCall:
    name: str
    args: dict = field(default_factory=dict)


@dataclass
class AgentResult:
    output: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    refused: bool = False
    error: str | None = None

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    def called(self, name: str) -> bool:
        return any(tc.name == name for tc in self.tool_calls)

    def calls_to(self, name: str) -> list[ToolCall]:
        return [tc for tc in self.tool_calls if tc.name == name]
