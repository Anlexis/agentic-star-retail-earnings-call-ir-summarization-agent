"""AgentCore Platform v1.0 — RET-C2-303 PreProcessNode (pre_process backbone)"""

# Input gate: size and content validation for the caller's IR document text,
# plus validation of the caller context channel.
# Node contract:
#   - Extend FunctionNode; implement execute(state) -> dict (partial state update)
#   - Return ONLY the fields this node changes (never the full state)
#   - Return AgentStatus enum constants — never plain strings
#   - read input_context via state.get("input_context", {}) — read-only
#   - Never import from mediator/, api/, or other agents

import json
from typing import Any, ClassVar

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from shared.utils.audit_logger import emit_trace_event

from src.services import screens

# ~2MB of text (a 200-page IR document is roughly 1–2M characters).
_MAX_INPUT_SIZE_CHARS: int = screens.MAX_INPUT_SIZE_CHARS


class PreProcessNode(FunctionNode):
    """Input gate for RET-C2-303.

    Validates the caller's IR document: non-empty, within the size bound, and
    free of the content classes this pipeline refuses. Validates the caller
    context channel against an inert identifier alphabet, because those values
    are rendered into the report.

    The refusal is owned here rather than delegated to the framework. A test
    that asserts "the framework refused it" passes only where that gate is
    active; where it is absent or configured off, the same payload reaches the
    answer path and returns success. Where the framework does refuse first, it
    refuses the same payloads this node would — never fewer.

    Assigned to the outer graph's `pre_process` slot. Requires
    VERIFIED_EXTERNAL: this is the external-facing gate.
    """

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.VERIFIED_EXTERNAL

    def execute(self, state: dict[str, Any], config: dict[str, Any] | None = None) -> dict[str, Any]:
        user_input: str = state.get("user_input", "")
        input_context: dict[str, Any] = state.get("input_context", {}) or {}  # read-only

        reason = self._first_refusal(user_input, input_context)
        if reason:
            emit_trace_event(
                "ret_c2_303.pre_process.rejected",
                {"reason": reason},
                state,
            )
            return {
                "status": AgentStatus.ERROR,
                # A closed-set reason code. The rejected value is never echoed,
                # and neither is the field's own name when the caller chose it.
                "error_log": [f"PreProcessNode: input rejected ({reason})"],
            }

        sanitized = user_input.strip()
        context = screens.normalise_context(input_context)

        emit_trace_event(
            "ret_c2_303.pre_process.accepted",
            {
                "input_size": len(sanitized),
                "channel": context.get("channel", "unknown"),
                "doc_language": context.get("doc_language", "auto"),
            },
            state,
        )

        # Carried to the inner graph by the context bridge and rendered in the
        # report header, so a declared channel or document language is visible
        # in the deliverable rather than parsed and dropped.
        enriched_context = json.dumps(
            {
                "source": "RetailEarningsCallIRSummarizationAgent",
                "channel": context.get("channel", "unknown"),
                "doc_language": context.get("doc_language", "auto"),
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )

        return {
            "validated_input": sanitized,
            "enriched_context": enriched_context,
            "status": AgentStatus.SUCCESS,
        }

    @staticmethod
    def _first_refusal(user_input: str, input_context: dict[str, Any]) -> str | None:
        """Return the first closed-set reason this request must be refused, if any."""
        if not user_input or not user_input.strip():
            return screens.REASON_EMPTY_INPUT

        sanitized = user_input.strip()
        if len(sanitized) > _MAX_INPUT_SIZE_CHARS:
            return screens.REASON_INPUT_TOO_LARGE

        reason = screens.screen_control_tokens(sanitized)
        if reason:
            return reason
        reason = screens.screen_injection_markers(sanitized)
        if reason:
            return reason

        # The context channel is screened depth-first including keys, so a
        # hostile field NAME is caught as well as a hostile value, and a
        # `\u`-escaped payload cannot evade a scan that runs after the parse.
        reason = screens.screen_structure(input_context)
        if reason:
            return reason

        for key in screens.SUPPORTED_CONTEXT_KEYS:
            value = input_context.get(key)
            if value is None:
                continue
            if not isinstance(value, str) or not screens.is_inert_identifier(value.strip().lower()):
                return screens.REASON_UNSUPPORTED_FIELD_VALUE
        return None
