"""AgentCore Platform v1.0 — RET-C2-303 IRDocumentParseNode (inner domain graph)"""

# Parses and normalizes the IR document text for downstream metric extraction.
# Node contract:
#   - Extend FunctionNode; implement execute(state) -> dict (partial state update)
#   - Return ONLY the fields this node changes (never the full state)
#   - Return AgentStatus enum constants — never plain strings [A1]
#   - required_trust_level = ANONYMOUS (inner subgraph node; backbone PreProcessNode
#     is the S-1 external-facing gate; inner nodes declare ANONYMOUS per review finding 5)
#   - Never import from mediator/, api/, or other agents

import re
from typing import Any, ClassVar

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from shared.utils.audit_logger import emit_trace_event

# Page break detection heuristics (Japanese and English IR docs)
_PAGE_BREAK_RE = re.compile(r"\[?(?:page|ページ|頁)\s*\d+\]?", re.IGNORECASE)
# Section header detection (common IR document markers)
_SECTION_HEADER_RE = re.compile(
    r"^(?:(?:\d+\.|[IVX]+\.)\s+|\s*[【「\[]\s*)[^\n]{3,60}$",
    re.MULTILINE,
)


def _normalize_document(text: str) -> str:
    """Module-level helper: normalize page breaks and whitespace in IR document text."""
    # Standardize page markers
    normalized = _PAGE_BREAK_RE.sub("\n--- PAGE BREAK ---\n", text)
    # Collapse multiple blank lines (preserve single blank lines as paragraph separators)
    normalized = re.sub(r"\n{3,}", "\n\n", normalized)
    # Remove trailing whitespace per line
    normalized = "\n".join(line.rstrip() for line in normalized.splitlines())
    return normalized.strip()


class IRDocumentParseNode(FunctionNode):
    """Parses and normalizes the IR document text for downstream extraction.

    In production this node would invoke a PDF parsing / OCR service.
    In this implementation it normalizes the text input (validates structure,
    cleans page breaks, collapses whitespace) for downstream metric extraction.

    Assigned to the inner domain graph (IRSummarizationDomainGraph).
    Trust level ANONYMOUS: inner subgraph node; outer PreProcessNode is the S-1 gate.
    """

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.ANONYMOUS

    def execute(self, state: dict[str, Any], config: dict[str, Any] | None = None) -> dict[str, Any]:
        validated_input: str = state.get("validated_input", state.get("user_input", ""))

        if not validated_input:
            emit_trace_event(
                "ret_c2_303.ir_document_parse.error",
                {"reason": "no_validated_input"},
                state,
            )
            return {
                "status": AgentStatus.ERROR,
                "error_log": ["IRDocumentParseNode: no validated_input in state"],
            }

        parsed_document = _normalize_document(validated_input)

        # Count detected section headers as a structural quality signal
        section_count = len(_SECTION_HEADER_RE.findall(parsed_document))

        emit_trace_event(
            "ret_c2_303.ir_document_parse.complete",
            {
                "original_size": len(validated_input),
                "parsed_size": len(parsed_document),
                "section_count": section_count,
            },
            state,
        )

        return {
            "parsed_document": parsed_document,
            "status": AgentStatus.SUCCESS,
        }
