"""AgentCore Platform v1.0 — RET-C2-303 ManagementGuidanceExtractNode (inner domain graph)"""

# Extracts forward-looking management guidance highlights from the IR document:
# executive commentary, full-year guidance, and strategic initiative statements.
# Node contract:
#   - Extend FunctionNode; implement execute(state) -> dict (partial state update)
#   - Return ONLY the fields this node changes (never the full state)
#   - Return AgentStatus enum constants — never plain strings
#   - required_trust_level = ANONYMOUS (inner subgraph node)

import re
from typing import Any, ClassVar, Optional

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from shared.utils.audit_logger import emit_trace_event

from src.services.screens import MAX_EXCERPT_CHARS, bounded_number, quote_excerpt

# Highlights are excerpts of the source document, so they are the one part of
# the report whose text the caller controls. Each is rendered as a single quoted
# line (see quote_excerpt) and the block as a whole is bounded, so a document
# cannot use its own body to add structure to the deliverable or to move bulk
# text through it.
_MAX_HIGHLIGHTS: int = 5
_MAX_GUIDANCE_CHARS: int = _MAX_HIGHLIGHTS * (MAX_EXCERPT_CHARS + 8) + 128

_NO_GUIDANCE_TEXT = "No explicit management guidance statements identified in document."

# Guidance section detection (Japanese and English earnings-call conventions).
_GUIDANCE_SECTION_RE = re.compile(
    r"(?:経営方針|ガイダンス|業績予想|見通し|今後の取り組み|中期経営計画"
    r"|guidance|outlook|forecast|forward.?looking|full.?year.?guidance"
    r"|strategic.?initiative|management.?commentary)[^\n]{0,200}",
    re.IGNORECASE,
)

# Executive attribution. The window between the title and the speech verb is
# wide enough for a quoted sentence to sit between them, which is the ordinary
# Japanese construction — a narrow window silently matched nothing.
_EXEC_ATTRIBUTION_RE = re.compile(
    r"(?:代表取締役|社長|CEO|CFO|取締役)[^\n]{0,120}?" r"(?:申し上げ|述べ|語り|said|comment)[^\n]{0,200}",
    re.IGNORECASE,
)

# Full-year revenue / profit targets. Same non-greedy gap and leading guard as
# the metric grammar: a greedy gap reported the last digit of the figure, so a
# ¥450bn target was rendered as "0".
_FY_TARGET_RE = re.compile(
    r"(?:通期|FY\d{4}|fiscal.?year)[^\n]{0,60}?"
    r"(?<![\d.,])([+-]?\d+(?:[,，]\d{3})*(?:\.\d+)?)"
    r"\s*(?:億円|百万円|兆円|円|million|billion)",
    re.IGNORECASE,
)

_FY_TARGET_MAX_ABS = 1e15
_MAX_FY_TARGETS = 3


def _extract_guidance_highlights(document: str) -> str:
    """Module-level helper: extract guidance highlights as quoted excerpts."""
    highlights: list[str] = []

    def add(raw: str) -> None:
        quoted = quote_excerpt(raw)
        if quoted not in highlights:
            highlights.append(quoted)

    for match in _GUIDANCE_SECTION_RE.finditer(document):
        add(match.group(0))
        if len(highlights) >= 3:
            break

    for match in _EXEC_ATTRIBUTION_RE.finditer(document):
        add(match.group(0))
        if len(highlights) >= _MAX_HIGHLIGHTS:
            break

    targets = [
        value
        for value in (bounded_number(raw, max_abs=_FY_TARGET_MAX_ABS) for raw in _FY_TARGET_RE.findall(document))
        if value is not None
    ]
    if targets:
        highlights.append("Full-year targets stated: " + ", ".join(targets[:_MAX_FY_TARGETS]))

    if not highlights:
        return _NO_GUIDANCE_TEXT

    guidance_text = "\n\n".join(highlights)
    if len(guidance_text) > _MAX_GUIDANCE_CHARS:
        guidance_text = guidance_text[:_MAX_GUIDANCE_CHARS]
    return guidance_text


class ManagementGuidanceExtractNode(FunctionNode):
    """Extracts forward-looking management guidance highlights from the IR document.

    Identifies executive commentary, full-year guidance statements and strategic
    initiative declarations from earnings-call transcripts and IR presentations.

    Highlights are quoted excerpts of the source document rather than verbatim
    reproductions: whitespace is collapsed to one line and the characters that
    carry structural meaning in the rendered report are substituted, so the
    document cannot add headings or table rows to the deliverable.

    Assigned to the inner domain graph (IRSummarizationDomainGraph).
    Trust level ANONYMOUS: inner subgraph node.
    """

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.ANONYMOUS

    def execute(self, state: dict[str, Any], config: dict[str, Any] | None = None) -> dict[str, Any]:
        parsed_document: Optional[str] = state.get("parsed_document")

        if not parsed_document:
            emit_trace_event(
                "ret_c2_303.management_guidance_extract.error",
                {"reason": "no_parsed_document"},
                state,
            )
            return {
                "status": AgentStatus.ERROR,
                "error_log": ["ManagementGuidanceExtractNode: no parsed_document in state"],
            }

        guidance = _extract_guidance_highlights(parsed_document)
        has_fy_target = bool(_FY_TARGET_RE.search(parsed_document))

        emit_trace_event(
            "ret_c2_303.management_guidance_extract.complete",
            {
                "guidance_length": len(guidance),
                "has_fy_target": has_fy_target,
            },
            state,
        )

        return {
            "management_guidance": guidance,
            "status": AgentStatus.SUCCESS,
        }
