"""AgentCore Platform v1.0 — RET-C2-303 RegulatoryRiskFlagNode (inner domain graph)"""

# Flags regulatory and compliance risk signals from the IR document.
# Detects: fair-trade / antitrust concerns, supply-chain regulation exposure,
# consumer protection flags, labor compliance issues, and cross-border trade risk.
# Node contract:
#   - Extend FunctionNode; implement execute(state) -> dict (partial state update)
#   - Return ONLY the fields this node changes (never the full state)
#   - Return AgentStatus enum constants — never plain strings [A1]
#   - required_trust_level = ANONYMOUS (inner subgraph node)
#   - ADR-005: list output → Optional[str] via JSON (regulatory_risks_json)

import json
import re
from typing import Any, ClassVar, Optional

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from shared.utils.audit_logger import emit_trace_event

# Regulatory risk signal definitions for Japan retail IR analysis
_RISK_PATTERNS: list[dict[str, Any]] = [
    {
        "risk_code": "REG-001",
        "severity": "HIGH",
        "description": "Antitrust / fair-trade concern (独占禁止法 / 優越的地位の濫用)",
        "pattern": re.compile(
            r"独占禁止|優越的地位|抱き合わせ|不公正な取引|antitrust|cartel|price.?fixing",
            re.IGNORECASE,
        ),
    },
    {
        "risk_code": "REG-002",
        "severity": "HIGH",
        "description": "Consumer protection exposure (景品表示法 / 特定商取引法)",
        "pattern": re.compile(
            r"景品表示|優良誤認|有利誤認|特定商取引|消費者保護|consumer.?protection" r"|misleading.?advert",
            re.IGNORECASE,
        ),
    },
    {
        "risk_code": "REG-003",
        "severity": "MEDIUM",
        "description": "Supply-chain regulation risk (物流総合効率化法 / 流通BMS)",
        "pattern": re.compile(
            r"物流総合効率化|物流2024年問題|ドライバー不足|supply.?chain.?regulat" r"|logistics.?law|流通BMS",
            re.IGNORECASE,
        ),
    },
    {
        "risk_code": "REG-004",
        "severity": "MEDIUM",
        "description": "Labor compliance issue (働き方改革 / 残業規制)",
        "pattern": re.compile(
            r"働き方改革|残業規制|時間外労働|労働基準|ハラスメント|labor.?reform"
            r"|overtime.?cap|employment.?violation",
            re.IGNORECASE,
        ),
    },
    {
        "risk_code": "REG-005",
        "severity": "MEDIUM",
        "description": "Cross-border / import trade risk (関税 / 輸入規制)",
        "pattern": re.compile(
            r"関税|輸入規制|輸出規制|貿易摩擦|trade.?tension|tariff|import.?restrict" r"|export.?control",
            re.IGNORECASE,
        ),
    },
    {
        "risk_code": "REG-006",
        "severity": "LOW",
        "description": "ESG / sustainability reporting obligation",
        "pattern": re.compile(
            r"ESG|サステナビリティ|脱炭素|CO2|温室効果ガス|人権デューデリジェンス"
            r"|sustainability.?report|climate.?disclosure|scope.?[123]",
            re.IGNORECASE,
        ),
    },
]


# The closed set of codes this template can report. The output boundary renders
# a risk row only for a code in this tuple, so a row is always a statement this
# template made rather than text a document supplied.
RISK_CODES: tuple[str, ...] = tuple(risk["risk_code"] for risk in _RISK_PATTERNS)


def _scan_risks(document: str) -> list[dict[str, str]]:
    """Module-level helper: scan document for all registered risk patterns."""
    detected = []
    for risk in _RISK_PATTERNS:
        if risk["pattern"].search(document):
            detected.append(
                {
                    "risk_code": risk["risk_code"],
                    "severity": risk["severity"],
                    "description": risk["description"],
                }
            )
    return detected


class RegulatoryRiskFlagNode(FunctionNode):
    """Flags regulatory and compliance risk signals in the IR document.

    Scans for Japan retail-specific regulatory concerns:
      REG-001: Antitrust / fair-trade (HIGH)
      REG-002: Consumer protection (HIGH)
      REG-003: Supply-chain regulation (MEDIUM)
      REG-004: Labor compliance (MEDIUM)
      REG-005: Cross-border trade risk (MEDIUM)
      REG-006: ESG reporting obligation (LOW)

    Produces regulatory_risks_json: JSON list of detected {risk_code, severity, description}.
    Assigned to the inner domain graph (IRSummarizationDomainGraph).
    """

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.ANONYMOUS

    def execute(self, state: dict[str, Any], config: dict[str, Any] | None = None) -> dict[str, Any]:
        parsed_document: Optional[str] = state.get("parsed_document")

        if not parsed_document:
            emit_trace_event(
                "ret_c2_303.regulatory_risk_flag.error",
                {"reason": "no_parsed_document"},
                state,
            )
            return {
                "status": AgentStatus.ERROR,
                "error_log": ["RegulatoryRiskFlagNode: no parsed_document in state"],
            }

        risks = _scan_risks(parsed_document)
        high_count = sum(1 for r in risks if r["severity"] == "HIGH")
        medium_count = sum(1 for r in risks if r["severity"] == "MEDIUM")

        emit_trace_event(
            "ret_c2_303.regulatory_risk_flag.complete",
            {
                "risks_detected": len(risks),
                "high_severity": high_count,
                "medium_severity": medium_count,
                "risk_codes": [r["risk_code"] for r in risks],
            },
            state,
        )

        return {
            "regulatory_risks_json": json.dumps(risks, ensure_ascii=False, separators=(",", ":")),
            "status": AgentStatus.SUCCESS,
        }
