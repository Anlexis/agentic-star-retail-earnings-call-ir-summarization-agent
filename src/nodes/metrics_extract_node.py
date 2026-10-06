"""AgentCore Platform v1.0 — RET-C2-303 MetricsExtractNode (inner domain graph)"""

# Extracts key financial metrics from the parsed IR document text:
# comparable-store sales growth, gross margin, inventory turns, and the
# AI/DX investment figure.
# Node contract:
#   - Extend FunctionNode; implement execute(state) -> dict (partial state update)
#   - Return ONLY the fields this node changes (never the full state)
#   - Return AgentStatus enum constants — never plain strings
#   - required_trust_level = ANONYMOUS (inner subgraph node)
#   - complex output → Optional[str] via JSON (financial_metrics_json)

import json
import re
from typing import Any, ClassVar, Optional

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from shared.utils.audit_logger import emit_trace_event

from src.services.screens import bounded_number

# ---------------------------------------------------------------------------
# Extraction grammar
# ---------------------------------------------------------------------------
#
# Two properties every pattern below depends on, and neither is optional:
#
#   The gap between the metric label and its value is NON-GREEDY. A greedy gap
#   consumes as much of the line as it can and then backtracks just far enough
#   for the value group to match — which lands on the LAST digit rather than the
#   first. "+4.8%" was read as "8", "32.5%" as "5", "8.4回" as "4" and "120億円"
#   as "0": each metric was reported as the final digit of its own value, sign
#   dropped, with nothing anywhere indicating a problem.
#
#   The value group carries a leading guard, `(?<![\d.,])`, so a match cannot
#   begin part-way through a number. Non-greedy matching alone still allows the
#   engine to start at the second digit if the first attempt fails, which
#   reproduces the same corruption in a narrower set of cases.
#
# tests/unit/test_metrics_extraction.py pins expected values for both scripts
# and both signs; an assertion that a number was merely "found" would have
# passed throughout the period this was wrong.

_VALUE = r"(?<![\d.,])([+-]?\d+(?:[,，]\d{3})*(?:\.\d+)?)"

# Comparable-store ("same-store") sales growth, as a percentage.
_SAME_STORE_RE = re.compile(
    r"(?:既存店|同一店舗|comparable.?store|same.?store)[^\n]{0,60}?" + _VALUE + r"\s*%",
    re.IGNORECASE,
)
# Gross margin, as a percentage or a movement in percentage points.
_GROSS_MARGIN_RE = re.compile(
    r"(?:粗利益?率?|gross.?margin)[^\n]{0,60}?" + _VALUE + r"\s*(?:pp|pt|%|ポイント)",
    re.IGNORECASE,
)
# Inventory turns, annualised.
_INVENTORY_TURNS_RE = re.compile(
    r"(?:在庫回転|inventory.?turn)[^\n]{0,60}?" + _VALUE + r"\s*(?:回|times|x)",
    re.IGNORECASE,
)
# AI / DX investment amount.
_AI_DX_RE = re.compile(
    r"(?:AI|DX|デジタル)[^\n]{0,80}?" + _VALUE + r"\s*(?:億円|百万円|万円|円|million|billion)",
    re.IGNORECASE,
)

# Bounds for the extracted values. A document is caller-controlled text, so an
# arbitrarily large run of digits is a possible input for every one of these
# fields; an out-of-range or non-finite value is reported as not found rather
# than rendered into the deliverable.
_PERCENT_MAX_ABS = 1_000.0
_TURNS_MAX_ABS = 10_000.0
_AMOUNT_MAX_ABS = 1e15

_FIELD_BOUNDS: dict[str, float] = {
    "same_store_sales_pct": _PERCENT_MAX_ABS,
    "gross_margin_delta_pct": _PERCENT_MAX_ABS,
    "inventory_turns": _TURNS_MAX_ABS,
    "ai_dx_investments_jpy": _AMOUNT_MAX_ABS,
}


def _extract_first_match(pattern: "re.Pattern[str]", text: str) -> Optional[str]:
    """Module-level helper: return the first capturing group, or None."""
    match = pattern.search(text)
    return match.group(1).strip() if match else None


def _extract_financial_metrics(document: str) -> dict[str, Optional[str]]:
    """Module-level helper: extract the structured financial metrics.

    A field is None when the pattern does not match, and also when the matched
    value does not survive its bound — the caller cannot tell the two apart,
    which is the intended behaviour: neither is a number this agent will report.
    """
    raw: dict[str, Optional[str]] = {
        "same_store_sales_pct": _extract_first_match(_SAME_STORE_RE, document),
        "gross_margin_delta_pct": _extract_first_match(_GROSS_MARGIN_RE, document),
        "inventory_turns": _extract_first_match(_INVENTORY_TURNS_RE, document),
        "ai_dx_investments_jpy": _extract_first_match(_AI_DX_RE, document),
    }
    return {field: bounded_number(value, max_abs=_FIELD_BOUNDS[field]) for field, value in raw.items()}


class MetricsExtractNode(FunctionNode):
    """Extracts key financial metrics from the parsed IR document.

    Produces financial_metrics_json with:
      - same_store_sales_pct: comparable-store sales growth, percent
      - gross_margin_delta_pct: gross margin, percent or percentage points
      - inventory_turns: inventory turn rate, annualised
      - ai_dx_investments_jpy: AI/DX investment amount

    A field is None when the document does not state it, or states a value
    outside the range this agent will report.
    Assigned to the inner domain graph (IRSummarizationDomainGraph).
    """

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.ANONYMOUS

    def execute(self, state: dict[str, Any], config: dict[str, Any] | None = None) -> dict[str, Any]:
        parsed_document: Optional[str] = state.get("parsed_document")

        if not parsed_document:
            emit_trace_event(
                "ret_c2_303.metrics_extract.error",
                {"reason": "no_parsed_document"},
                state,
            )
            return {
                "status": AgentStatus.ERROR,
                "error_log": ["MetricsExtractNode: no parsed_document in state"],
            }

        metrics = _extract_financial_metrics(parsed_document)
        found_count = sum(1 for v in metrics.values() if v is not None)

        emit_trace_event(
            "ret_c2_303.metrics_extract.complete",
            {
                "metrics_found": found_count,
                "metrics_total": len(metrics),
                "has_same_store_sales": metrics["same_store_sales_pct"] is not None,
                "has_ai_dx": metrics["ai_dx_investments_jpy"] is not None,
            },
            state,
        )

        return {
            "financial_metrics_json": json.dumps(metrics, ensure_ascii=False, separators=(",", ":")),
            "status": AgentStatus.SUCCESS,
        }
