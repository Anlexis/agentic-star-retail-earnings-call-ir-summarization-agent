"""AgentCore Platform v1.0 — RET-C2-303 State"""

# ADR-005: State must be a flat TypedDict — never Pydantic BaseModel.
# LangGraph checkpoints use msgpack serialization; Pydantic objects
# cause silent corruption.  Extend AgentState with agent-specific
# fields only.  Do NOT add credentials, secrets, or Pydantic models.
#
# Complex data (dict / list) MUST be serialized as Optional[str] using
# json.dumps / json.loads in the producing / consuming node respectively.
# See to_json / from_json helpers below.

from __future__ import annotations

import json
from typing import Optional

from framework.schemas.agent_state import AgentState


# ---------------------------------------------------------------------------
# JSON helpers (ADR-005: dict/list → Optional[str] in flat TypedDict)
# ---------------------------------------------------------------------------


def to_json(obj: object) -> str:
    """Serialize a dict or list to a compact JSON string for State storage."""
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def from_json(raw: Optional[str]) -> object:
    """Deserialize a JSON string from State back to a dict or list.
    Returns None when raw is None or empty.
    """
    if not raw:
        return None
    return json.loads(raw)


# ---------------------------------------------------------------------------
# Agent State
# ---------------------------------------------------------------------------


class State(AgentState):
    """Flat TypedDict state for RET-C2-303 Retail Earnings Call IR Summarization.

    All fields storing structured data (lists / dicts) use Optional[str] with
    JSON serialization (to_json / from_json helpers above) to satisfy ADR-005
    msgpack-safe requirement.

    Field population order mirrors the node pipeline:
      PreProcessNode (pre_process backbone):
        → validated_input, enriched_context
      Inner domain graph (main slot, via IRSummarizationWorkflowGraphNode):
        IRDocumentParseNode             → parsed_document
        MetricsExtractNode              → financial_metrics_json
        ManagementGuidanceExtractNode   → management_guidance
        RegulatoryRiskFlagNode          → regulatory_risks_json
        StructuredSummaryFormatNode     → structured_summary
      IRSummarizationWorkflowGraphNode.merge_output:
        → result (mapped from structured_summary)
      PostProcessNode (post_process backbone):
        → formatted_output
    """

    # ── pre_process output ───────────────────────────────────────────────────
    validated_input: Optional[str]
    """Sanitized IR document text after S-1 size / injection validation."""

    enriched_context: Optional[str]
    """JSON-serialized dict: {source, channel, doc_language}."""

    # ── inner domain node outputs ────────────────────────────────────────────
    parsed_document: Optional[str]
    """Normalized IR document text (page breaks cleaned, whitespace normalized)."""

    financial_metrics_json: Optional[str]
    """JSON: {
      same_store_sales_pct: str | None,
      gross_margin_delta_pct: str | None,
      inventory_turns: str | None,
      ai_dx_investments_jpy: str | None
    }
    Key financial metrics extracted from the earnings call / IR document.
    """

    management_guidance: Optional[str]
    """Forward-looking management guidance highlights (CEO/CFO commentary text)."""

    regulatory_risks_json: Optional[str]
    """JSON list: [{risk_code: str, severity: str, description: str}]
    Regulatory and compliance risk signals flagged from the document.
    Severity: HIGH | MEDIUM | LOW
    """

    structured_summary: Optional[str]
    """Final structured IR summary: JSON metrics block + markdown guidance/risk section.
    Does NOT contain raw bulk document text (S-3 gate enforced in PostProcessNode).
    """

    # ── outer graph merge_output / post_process ──────────────────────────────
    result: Optional[str]
    """Set by IRSummarizationWorkflowGraphNode.merge_output(); equals
    structured_summary from the inner graph's get_output().
    """

    formatted_output: Optional[str]
    """Set by PostProcessNode after S-3 gate; final caller-facing output
    consumed by FinalizeNode.
    """
