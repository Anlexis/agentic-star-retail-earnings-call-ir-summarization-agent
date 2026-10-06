"""AgentCore Platform v1.0 — RET-C2-303 caller-context bridge (outer → inner graph)"""

# The framework's GraphNode calls `subgraph.invoke(user_input, session_id=…, ctx=…)`
# and passes no input_context, so a nested Cat 2 template's inner graph never sees
# the caller's context channel however carefully the outer graph validated it.
#
# The bridge carries the ALREADY-VALIDATED context across that one call: the outer
# graph's extract_input() stashes it immediately before the framework invokes the
# subgraph, and the inner graph's _extra_initial_state() seeds it into the inner
# state. Both happen inside one synchronous GraphNode.execute() call, and a
# ContextVar is per-execution-context, so concurrent invocations cannot read each
# other's value.
#
# Only the validated form travels. Raw caller context never crosses this bridge:
# the entry adapter drops unsupported keys, and the entry node rejects values that
# are not inert identifiers, so anything reaching the inner graph has already been
# through both.

from __future__ import annotations

from contextvars import ContextVar

_CALLER_CONTEXT: ContextVar[str] = ContextVar("ret_c2_303_caller_context", default="")


def stash(enriched_context: str | None) -> None:
    """Record the validated context for the subgraph invocation that follows."""
    _CALLER_CONTEXT.set(enriched_context or "")


def current() -> str:
    """Return the validated context stashed for this invocation, or ""."""
    return _CALLER_CONTEXT.get()
