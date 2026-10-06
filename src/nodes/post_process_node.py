"""AgentCore Platform v1.0 — RET-C2-303 PostProcessNode (post_process backbone)"""

# The output boundary: the last node that can decide what the caller receives.
# Node contract:
#   - Extend FunctionNode; implement execute(state) -> dict (partial state update)
#   - Return ONLY the fields this node changes (never the full state)
#   - Return AgentStatus enum constants — never plain strings
#   - Never import from mediator/, api/, or other agents
#
# Two things about the envelope decide how this node has to be written.
#
# The framework surfaces `formatted_output or result`. An error return that
# simply omits formatted_output therefore does not withhold anything: the
# fallback publishes the un-gated inner text instead, error status and all. And
# an EMPTY formatted_output is falsy, so it re-opens the same fallback. Both
# output-bearing fields are cleared here and the replacement is a non-empty
# notice.
#
# The notice is assembled from fixed labels. The error log is not part of it:
# it carries node-authored text and, wherever a node interpolates a caught
# exception, whatever that exception was carrying — so publishing it is not a
# closed error contract, whatever truncation or redaction is applied first.

from typing import Any, ClassVar

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from shared.utils.audit_logger import emit_trace_event

from src.nodes.structured_summary_format_node import (
    REPORT_HEADINGS,
    RISK_TABLE_HEADER,
    RISK_TABLE_RULE,
)
from src.nodes.regulatory_risk_flag_node import RISK_CODES
from src.services import screens

_WITHHELD_TITLE = "## Output Withheld"


def _withheld_notice(reason: str) -> str:
    """Build the caller-facing notice for a withheld report.

    Closed-set labels only: the reason is one of screens.REASON_CODES and
    nothing else is interpolated. Non-empty by construction, because a falsy
    replacement would re-open the framework's fallback to the inner text.
    """
    if reason not in screens.REASON_CODES:
        reason = screens.REASON_UPSTREAM_FAILURE
    return (
        f"{_WITHHELD_TITLE}\n\n"
        "The generated summary did not satisfy this agent's output contract and "
        "has been withheld.\n\n"
        f"Reason code: {reason}"
    )


def _contain(reason: str, state: dict[str, Any], event: str) -> dict[str, Any]:
    """Return an error delta that carries no part of the withheld report.

    Every output-bearing field is present and empty. A partial delta is merged
    into the existing state, so a field left out of this dict keeps the value it
    already had — omitting one is the same as publishing it.
    """
    emit_trace_event(event, {"reason": reason}, state)
    return {
        "formatted_output": _withheld_notice(reason),
        "result": "",
        "structured_summary": "",
        "management_guidance": "",
        "status": AgentStatus.ERROR,
        "error_log": [f"PostProcessNode: output withheld ({reason})"],
    }


def _structure_violation(report: str) -> bool:
    """True when *report* is not shaped like a report this template produces.

    An independent check on the assembled text, not a re-run of the rendering
    logic: the headings must be exactly the expected ones in the expected
    order, and every table row must name a risk code from the closed set. If a
    document ever succeeds in contributing structure to the deliverable, the
    node that rendered it is not the thing that catches it.
    """
    position = -1
    for heading in REPORT_HEADINGS:
        found = report.find(f"\n{heading}\n") if position >= 0 else report.find(heading)
        if found <= position:
            return True
        position = found

    for line in report.splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        if stripped in (RISK_TABLE_HEADER, RISK_TABLE_RULE):
            continue
        cells = [c.strip() for c in stripped.strip("|").split("|")]
        if len(cells) != 3 or cells[0] not in RISK_CODES:
            return True

    # A heading marker anywhere other than the three expected headings means the
    # report gained a section from somewhere.
    for line in report.splitlines():
        if line.startswith("#") and line.strip() not in REPORT_HEADINGS:
            return True
    return False


class PostProcessNode(FunctionNode):
    """Output boundary for RET-C2-303.

    Enforces two invariants on the assembled report before the caller sees it:

    1. No credential-shaped string. The shared screen is the union of the
       framework's own detector and the assignment-form markers the framework
       does not carry — the two sets are disjoint in both directions, so either
       one alone would be narrower than the gate this template advertises.
    2. The report has this template's own structure: the three expected
       headings in order, and table rows only for risk codes it defines.

    On a violation both output-bearing fields are cleared and the caller
    receives a fixed notice carrying a closed-set reason code.

    Assigned to the outer graph's `post_process` slot.
    Trust level ANONYMOUS: a backbone node, not an external gate.
    """

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.ANONYMOUS

    def execute(self, state: dict[str, Any], config: dict[str, Any] | None = None) -> dict[str, Any]:
        result = state.get("result") or ""

        if not result:
            # Reaching the output boundary with nothing to publish is a failed
            # run. Reporting success here would tell the caller the empty answer
            # was the answer.
            return _contain(
                screens.REASON_UPSTREAM_FAILURE,
                state,
                "ret_c2_303.post_process.no_result",
            )

        if screens.detect_credential_labels(result):
            return _contain(
                screens.REASON_CREDENTIAL_SHAPE,
                state,
                "ret_c2_303.post_process.output_blocked",
            )

        if _structure_violation(result):
            return _contain(
                screens.REASON_OUTPUT_STRUCTURE,
                state,
                "ret_c2_303.post_process.structure_blocked",
            )

        emit_trace_event(
            "ret_c2_303.post_process.complete",
            {"output_length": len(result)},
            state,
        )

        return {
            "formatted_output": result,
            "status": AgentStatus.SUCCESS,
        }
