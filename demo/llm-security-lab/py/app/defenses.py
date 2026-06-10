"""
app/defenses.py
===============
Defense-in-depth controls for LLM01 (Prompt Injection) and LLM02 (Sensitive
Information Disclosure). No single control is sufficient -- the point of the lab
is that you layer them, exactly as you would layer controls in a payments system.

Layers implemented here:
  1. INPUT GUARD       -- heuristic detection of override-style instructions.
  2. DATA DELIMITING   -- wrap untrusted content and instruct the model to treat
                          it strictly as data (structured prompting).
  3. OUTPUT GUARD      -- scan model output for known secrets and redact them,
                          as a last-resort backstop if layers 1-2 are bypassed.

These are illustrative, not exhaustive. Real deployments add allow-listed tools,
least-privilege credentials, human-in-the-loop on high-risk actions, and
output schema validation. See the README crosswalk.
"""

from __future__ import annotations

import re

# Patterns that commonly signal a direct prompt-injection attempt. Heuristics
# like this reduce risk but WILL miss novel phrasings -- which is precisely why
# we never rely on the input guard alone.
INJECTION_PATTERNS = [
    r"ignore (the )?(previous|above|prior)",
    r"disregard (the )?(previous|above|all)",
    r"\bsystem prompt\b",
    r"\bsystem instructions\b",
    r"\boperator key\b",
    r"\breveal\b",
    r"\bpwned\b",
    r"\bexfiltrat",
]

REFUSAL = (
    "I can't help with that request. If you have a question about your "
    "payments account, I'm happy to help with that instead."
)


def looks_like_injection(text: str) -> bool:
    low = text.lower()
    return any(re.search(p, low) for p in INJECTION_PATTERNS)


def wrap_untrusted(text: str) -> str:
    """Delimit untrusted content so the model can distinguish data from instructions."""
    # Strip any attempt to close our delimiter early.
    safe = text.replace("</untrusted_data>", "<\\/untrusted_data>")
    return f"<untrusted_data>\n{safe}\n</untrusted_data>"


def scan_output_for_secrets(text: str, secrets: list[str]) -> str:
    """Redact known secrets from model output (backstop against leakage)."""
    out = text
    for s in secrets:
        if s and s in out:
            out = out.replace(s, "[REDACTED]")
    return out
