"""
gateway/gateway.py
==================
The gateway. Every tool call from an agent passes through `SecureGateway.call`,
which enforces, in order and failing closed at each step:

  1. authenticate the caller to a principal,
  2. confirm the tool exists and is brokered here,
  3. check the default-deny allow-list for that principal,
  4. validate the arguments against the tool's schema and content guards,
  5. invoke the tool,
  6. sanitise the result before it returns to the model,
  7. record the decision in the audit log.

If any check before step five fails, the tool is never invoked, so a denied
call has no side effect. `NaiveProxy` is the same surface with none of the
checks, included so the demo and tests can show exactly what the gateway stops.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

from gateway.audit import AuditLog
from gateway.auth import AuthError, Authenticator
from gateway.policy import Policy, ToolSpec
from gateway.validation import InputValidationError, scan_output, validate_input

# A registered tool: its spec plus a handler that takes the validated args dict.
Handler = Callable[[dict], object]
Tool = tuple[ToolSpec, Handler]


@dataclass
class CallResult:
    ok: bool
    decision: str            # "allow" or "deny"
    reason: str
    principal: str = ""
    tool: str = ""
    output: object = None
    findings: list[str] = field(default_factory=list)


class SecureGateway:
    def __init__(
        self,
        *,
        tools: dict[str, Tool],
        policy: Policy,
        authenticator: Authenticator,
        audit: AuditLog,
        secrets: Iterable[str] = (),
    ):
        self.tools = tools
        self.policy = policy
        self.authenticator = authenticator
        self.audit = audit
        self.secrets = tuple(secrets)

    def _deny(self, principal: str, tool: str, reason: str) -> CallResult:
        self.audit.record(principal=principal, tool=tool, decision="deny", reason=reason)
        return CallResult(ok=False, decision="deny", reason=reason, principal=principal, tool=tool)

    def call(self, token: str | None, tool_name: str, args: dict) -> CallResult:
        # 1. Authenticate.
        try:
            principal = self.authenticator.authenticate(token)
        except AuthError as exc:
            return self._deny("<unauthenticated>", tool_name, f"auth: {exc}")

        # 2. Known tool?
        entry = self.tools.get(tool_name)
        if entry is None:
            return self._deny(principal, tool_name, "unknown tool")
        spec, handler = entry

        # 3. Allow-list (the confused-deputy defence).
        if not self.policy.allows(principal, tool_name):
            return self._deny(principal, tool_name, "not on this principal's allow-list")

        # 4. Input validation.
        try:
            validate_input(spec.input_schema, args)
        except InputValidationError as exc:
            return self._deny(principal, tool_name, f"input rejected: {exc}")

        # 5. Invoke. A tool that throws is contained, not propagated as a 200.
        try:
            raw = handler(args)
        except Exception as exc:  # noqa: BLE001 -- contain any tool failure
            return self._deny(principal, tool_name, f"tool raised: {exc}")

        # 6. Output sanitisation.
        output, findings = scan_output(str(raw), self.secrets)

        # 7. Audit the allow.
        self.audit.record(
            principal=principal, tool=tool_name, decision="allow", reason="ok", findings=findings
        )
        return CallResult(
            ok=True, decision="allow", reason="ok", principal=principal,
            tool=tool_name, output=output, findings=findings,
        )


class NaiveProxy:
    """An open proxy: invokes any tool with any args, no checks. The 'before'."""

    def __init__(self, tools: dict[str, Tool]):
        self.tools = tools

    def call(self, tool_name: str, args: dict) -> object:
        _, handler = self.tools[tool_name]
        return handler(args)
