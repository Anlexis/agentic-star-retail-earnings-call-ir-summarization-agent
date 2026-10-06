"""AgentCore Platform v1.0 — RET-C2-303 inner domain graph (Cat 2)"""

# Inner domain workflow graph for RET-C2-303.
# Instantiated by IRSummarizationWorkflowGraphNode.get_subgraph() in graph.py.
#
# Pipeline (linear):
#   START
#     → ir_document_parse          (normalize IR doc text)
#     → metrics_extract            (financial metrics: same-store sales, margins, AI/DX)
#     → management_guidance_extract (CEO/CFO forward guidance highlights)
#     → regulatory_risk_flag       (regulatory / compliance risk signals)
#     → structured_summary_format  (JSON + markdown structured summary output)
#   → END
#
# Rules:
#   ✅ Inherits BaseGraph (fully custom topology, no pre_process/main/post_process slots)
#   ✅ Implements all 7 BaseGraph abstract methods
#   ✅ register_nodes() does NOT call super() (abstract in BaseGraph)
#   ✅ Inner domain nodes instantiated with NO ctor args (SDK-v1 no-arg contract)
#   ✅ required_trust_level = ANONYMOUS on all inner domain nodes (review finding 5)
#   ✅ get_output() designed together with outer merge_output()
#   ❌ Do NOT register initialize / finalize (outer backbone concern)

from typing import Any

from langgraph.graph import END, START

from framework.graph.base_graph import BaseGraph
from framework.schemas.agent_state import AgentState
from framework.schemas.agent_status import AgentStatus

from src.graph import context_bridge
from src.nodes.ir_document_parse_node import IRDocumentParseNode
from src.nodes.management_guidance_extract_node import ManagementGuidanceExtractNode
from src.nodes.metrics_extract_node import MetricsExtractNode
from src.nodes.regulatory_risk_flag_node import RegulatoryRiskFlagNode
from src.nodes.structured_summary_format_node import StructuredSummaryFormatNode
from src.schemas.state import State


class IRSummarizationDomainGraph(BaseGraph):
    """Inner domain workflow graph for RET-C2-303.

    Runs the full IR document analysis pipeline from normalized text to
    structured summary report.
    Called by IRSummarizationWorkflowGraphNode.get_subgraph() in graph.py.

    Pipeline (linear):
      START
        → ir_document_parse          (DocumentParseNode analog — normalize text)
        → metrics_extract            (extract financial metrics)
        → management_guidance_extract (extract forward guidance)
        → regulatory_risk_flag       (flag regulatory risks)
        → structured_summary_format  (assemble final structured output)
      → END
    """

    # ── Identity ──────────────────────────────────────────────────────────────

    @property
    def name(self) -> str:
        """Unique identifier for this inner domain workflow graph."""
        return "ret_c2_303_ir_summarization_domain"

    @property
    def state_schema(self) -> type:
        return State

    # ── Caller context seeding ────────────────────────────────────────────────

    def _extra_initial_state(self) -> dict[str, Any]:
        """Seed the validated caller context the outer graph stashed.

        Node execute() methods take no config argument, and the framework
        invokes a subgraph without a context channel, so seeding the inner
        state is the only route a caller-declared value can travel into a
        domain node. The value is already validated — see context_bridge.
        """
        enriched_context = context_bridge.current()
        return {"enriched_context": enriched_context} if enriched_context else {}

    # ── Config validation ─────────────────────────────────────────────────────

    def _validate_config(self) -> None:
        """No mandatory config for the domain workflow stub."""
        pass

    # ── Node registration ─────────────────────────────────────────────────────

    def register_nodes(self) -> None:
        """Register all inner domain nodes with NO ctor args (SDK-v1 contract).

        ⚠️ Every node class is instantiated as NodeClass() — never NodeClass(arg).
        SDK-v1 FunctionNode has no __init__; config flows per-call via config=None.
        """
        self._nodes["ir_document_parse"] = IRDocumentParseNode()
        self._nodes["metrics_extract"] = MetricsExtractNode()
        self._nodes["management_guidance_extract"] = ManagementGuidanceExtractNode()
        self._nodes["regulatory_risk_flag"] = RegulatoryRiskFlagNode()
        self._nodes["structured_summary_format"] = StructuredSummaryFormatNode()

    # ── Edge wiring ───────────────────────────────────────────────────────────

    def add_edges(self) -> None:
        """Wire the linear domain pipeline.

        All edges are deterministic — route() is implemented (required by BaseGraph ABC)
        but no conditional branching is used in this pipeline.
        """
        self._sg.add_edge(START, "ir_document_parse")
        self._sg.add_edge("ir_document_parse", "metrics_extract")
        self._sg.add_edge("metrics_extract", "management_guidance_extract")
        self._sg.add_edge("management_guidance_extract", "regulatory_risk_flag")
        self._sg.add_edge("regulatory_risk_flag", "structured_summary_format")
        self._sg.add_edge("structured_summary_format", END)

    # ── Routing ───────────────────────────────────────────────────────────────

    def route(self, state: AgentState) -> str:
        """Required by BaseGraph ABC; not called in a linear topology.

        Implement meaningful branching here if conditional edges are added.
        """
        return END if state.get("status") == AgentStatus.ERROR.value else "structured_summary_format"

    # ── Output shape ──────────────────────────────────────────────────────────

    def get_output(self, state: AgentState) -> dict[str, Any]:
        """Shape the output dict returned to IRSummarizationWorkflowGraphNode.merge_output().

        merge_output() in graph.py maps:
          structured_summary → result  (consumed by PostProcessNode in post_process)
          status              → status
        """
        return {
            "structured_summary": state.get("structured_summary"),
            "status": state.get("status"),
            "trace_id": state.get("trace_id"),
            "correlation_id": state.get("correlation_id"),
            "node_history": state.get("node_history", []),
        }
