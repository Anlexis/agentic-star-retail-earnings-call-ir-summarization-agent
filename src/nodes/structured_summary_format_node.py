"""AgentCore Platform v1.0 — RET-C2-303 StructuredSummaryFormatNode (inner domain graph)"""

# Assembles the structured IR summary from the upstream extraction outputs:
# a metrics block, a quoted guidance block, and a regulatory risk table.
# Node contract:
#   - Extend FunctionNode; implement execute(state) -> dict (partial state update)
#   - Return ONLY the fields this node changes (never the full state)
#   - Return AgentStatus enum constants — never plain strings
#   - required_trust_level = ANONYMOUS (inner subgraph node)

import json
from typing import Any, ClassVar, Optional

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from shared.utils.audit_logger import emit_trace_event

from src.nodes.regulatory_risk_flag_node import RISK_CODES
from src.schemas.state import from_json
from src.services import screens

# The report's structure is fixed. These headings, in this order, are the whole
# of it — the output boundary checks the assembled text against this list, so a
# document that manages to add a heading of its own is caught by something other
# than the code that rendered it.
REPORT_HEADINGS: tuple[str, ...] = (
    "## Financial Metrics",
    "## Management Forward Guidance",
    "## Regulatory Risk Signals",
)

RISK_TABLE_HEADER = "| Risk Code | Severity | Description |"
RISK_TABLE_RULE = "|-----------|----------|-------------|"

_NO_RISKS_TEXT = "No regulatory risk signals detected."

# Upper bound on the assembled report.
_MAX_SUMMARY_CHARS: int = 10_000

_SEVERITY_ORDER: dict[str, int] = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}


def _format_metrics_block(metrics: object) -> str:
    """Module-level helper: format the financial metrics as a JSON block."""
    if not isinstance(metrics, dict) or not metrics:
        return '{"error": "metrics not available"}'
    return json.dumps(metrics, ensure_ascii=False, indent=2)


def _format_risk_table(risks: object) -> str:
    """Module-level helper: format the regulatory risks as a markdown table.

    Only rows whose risk code belongs to this template's own closed set are
    rendered, and every cell is drawn from the template's own definitions —
    never from the document — so a table row is always this agent's statement.
    """
    if not isinstance(risks, list) or not risks:
        return _NO_RISKS_TEXT

    known = [r for r in risks if isinstance(r, dict) and r.get("risk_code") in RISK_CODES]
    if not known:
        return _NO_RISKS_TEXT

    ordered = sorted(known, key=lambda r: _SEVERITY_ORDER.get(r.get("severity", "LOW"), 3))
    lines = [RISK_TABLE_HEADER, RISK_TABLE_RULE]
    for risk in ordered:
        lines.append(f"| {risk['risk_code']} | {risk.get('severity', '—')} | {risk.get('description', '—')} |")
    return "\n".join(lines)


def _format_provenance(enriched_context: Optional[str]) -> str:
    """Module-level helper: render the caller-declared context on one line.

    Both values reached this node through the entry gate and the context bridge,
    so both are inert identifiers by the time they are rendered.
    """
    context = from_json(enriched_context) if enriched_context else None
    if not isinstance(context, dict):
        return ""
    channel = context.get("channel", "unknown")
    language = context.get("doc_language", "auto")
    if not screens.is_inert_identifier(str(channel)):
        channel = "unknown"
    if not screens.is_inert_identifier(str(language)):
        language = "auto"
    return f"Source channel: {channel} · Document language: {language}\n\n"


def _build_structured_summary(
    financial_metrics_json: Optional[str],
    management_guidance: Optional[str],
    regulatory_risks_json: Optional[str],
    enriched_context: Optional[str],
) -> str:
    """Module-level helper: assemble the structured IR summary.

    Bulk document text is not reproduced. The guidance block carries quoted
    single-line excerpts only; everything else in the report is this template's
    own text or a bounded number.
    """
    metrics = from_json(financial_metrics_json) if financial_metrics_json else None
    risks = from_json(regulatory_risks_json) if regulatory_risks_json else None
    guidance = management_guidance or "No management guidance extracted."

    summary = (
        f"{_format_provenance(enriched_context)}"
        f"{REPORT_HEADINGS[0]}\n\n"
        "```json\n"
        f"{_format_metrics_block(metrics)}\n"
        "```\n\n"
        f"{REPORT_HEADINGS[1]}\n\n"
        f"{guidance}\n\n"
        f"{REPORT_HEADINGS[2]}\n\n"
        f"{_format_risk_table(risks)}"
    )

    if len(summary) > _MAX_SUMMARY_CHARS:
        summary = summary[:_MAX_SUMMARY_CHARS] + "\n\n[...output truncated at the size limit]"

    return summary


class StructuredSummaryFormatNode(FunctionNode):
    """Assembles the structured IR summary from the upstream extraction outputs.

    Combines:
      - financial_metrics_json (from MetricsExtractNode)
      - management_guidance (from ManagementGuidanceExtractNode)
      - regulatory_risks_json (from RegulatoryRiskFlagNode)

    Produces structured_summary: a provenance line, a financial metrics JSON
    block, a quoted management guidance block, and a regulatory risk table.

    Credential screening here is the producing node's own check. It uses the
    same shared screen as the output boundary, so the two can never enforce
    different rules — a value one of them blocks is a value the other blocks.

    Assigned to the inner domain graph (IRSummarizationDomainGraph).
    """

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.ANONYMOUS

    def execute(self, state: dict[str, Any], config: dict[str, Any] | None = None) -> dict[str, Any]:
        financial_metrics_json: Optional[str] = state.get("financial_metrics_json")
        management_guidance: Optional[str] = state.get("management_guidance")
        regulatory_risks_json: Optional[str] = state.get("regulatory_risks_json")

        if not any([financial_metrics_json, management_guidance, regulatory_risks_json]):
            emit_trace_event(
                "ret_c2_303.structured_summary_format.error",
                {"reason": screens.REASON_UPSTREAM_FAILURE},
                state,
            )
            return {
                "status": AgentStatus.ERROR,
                "error_log": ["StructuredSummaryFormatNode: no upstream extraction results in state"],
            }

        structured_summary = _build_structured_summary(
            financial_metrics_json,
            management_guidance,
            regulatory_risks_json,
            state.get("enriched_context"),
        )

        labels = screens.detect_credential_labels(structured_summary)
        if labels:
            emit_trace_event(
                "ret_c2_303.structured_summary_format.output_blocked",
                {"reason": screens.REASON_CREDENTIAL_SHAPE, "labels": labels},
                state,
            )
            # The assembled text is not carried forward in any form. Returning
            # it alongside an error status would publish exactly the text this
            # branch exists to withhold.
            return {
                "structured_summary": "",
                "status": AgentStatus.ERROR,
                "error_log": ["StructuredSummaryFormatNode: output withheld " f"({screens.REASON_CREDENTIAL_SHAPE})"],
            }

        risks = from_json(regulatory_risks_json) if regulatory_risks_json else []
        risk_list = risks if isinstance(risks, list) else []
        high_risks = sum(1 for r in risk_list if isinstance(r, dict) and r.get("severity") == "HIGH")

        emit_trace_event(
            "ret_c2_303.structured_summary_format.complete",
            {
                "summary_length": len(structured_summary),
                "risk_count": len(risk_list),
                "high_risks": high_risks,
            },
            state,
        )

        return {
            "structured_summary": structured_summary,
            "status": AgentStatus.SUCCESS,
        }
