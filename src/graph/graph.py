"""AgentCore Platform v1.0 — RET-C2-303 outer graph (Cat 2)"""

# Cat 2 outer graph — AgentBaseGraph + GraphNode in the `main` slot.
#
# Architecture:
#   Outer backbone (fixed, standard 5-node):
#     START → initialize → pre_process → main → {route} → post_process → finalize → END
#
#   Slot mapping for RET-C2-303:
#     pre_process  ← PreProcessNode  (S-1 external gate, VERIFIED_EXTERNAL)
#     main         ← IRSummarizationWorkflowGraphNode (GraphNode → inner domain graph)
#     post_process ← PostProcessNode  (S-3 output gate, ANONYMOUS)
#
#   Inner graph (src/graph/domain_workflow_graph.py):
#     START → ir_document_parse → metrics_extract → management_guidance_extract
#           → regulatory_risk_flag → structured_summary_format → END
#
# Rules:
#   ✅ Outer graph inherits AgentBaseGraph
#   ✅ Call super().register_nodes() in outer graph (injects initialize + finalize)
#   ✅ Assign GraphNode subclass to the `main` slot
#   ✅ Inner graph at src/graph/domain_workflow_graph.py
#   ✅ Class name matches the manifest `class:` entry point and the server import
#   ❌ Do NOT override add_edges() on the outer graph
#   ❌ No ctor args on inner domain nodes (SDK-v1 no-arg contract)

from typing import TYPE_CHECKING, Any, ClassVar

from framework.graph.agent_base_graph import AgentBaseGraph
from framework.nodes.graph_node import GraphNode
from framework.schemas.agent_state import AgentState

from src.graph import context_bridge
from src.nodes.post_process_node import PostProcessNode
from src.nodes.pre_process_node import PreProcessNode
from src.schemas.state import State

if TYPE_CHECKING:
    from src.graph.domain_workflow_graph import IRSummarizationDomainGraph


class IRSummarizationWorkflowGraphNode(GraphNode):
    """Wraps the inner IRSummarizationDomainGraph; assigned to the `main` slot.

    Orchestrates the full domain pipeline:
      IRDocumentParse → MetricsExtract → ManagementGuidanceExtract →
      RegulatoryRiskFlag → StructuredSummaryFormat
    """

    error_strategy: ClassVar[str] = "propagate"
    propagate_hitl: ClassVar[bool] = False

    def get_subgraph(self) -> "IRSummarizationDomainGraph":
        """Instantiate and return the inner IR summarization domain graph."""
        from src.graph.domain_workflow_graph import IRSummarizationDomainGraph

        return IRSummarizationDomainGraph()

    def extract_input(self, state: AgentState) -> str:
        """Pass validated_input (from PreProcessNode) into the inner graph.

        The framework invokes the subgraph with the user input alone and carries
        no context channel across, so the validated caller context is stashed
        here — immediately before that call — and seeded by the inner graph's
        _extra_initial_state(). Without it the inner pipeline cannot render the
        caller's declared channel or document language, and the context contract
        the entry node validates would have no effect on anything.
        """
        context_bridge.stash(state.get("enriched_context"))
        return str(state.get("validated_input") or state.get("user_input") or "")

    def merge_output(self, state: AgentState, sub_result: dict[str, Any]) -> dict[str, Any]:
        """Map inner graph get_output() fields back into the outer state.

        sub_result fields (from IRSummarizationDomainGraph.get_output()):
          structured_summary → result  (consumed by PostProcessNode in post_process)
          status              → status
        """
        return {
            "result": sub_result.get("structured_summary"),
            "status": sub_result.get("status"),
        }


class RetailEarningsCallIRSummarizationAgent(AgentBaseGraph):
    """Cat 2 outer graph for RET-C2-303.

    Backbone: initialize → pre_process → main → post_process → finalize (fixed).
    Domain complexity is encapsulated in IRSummarizationWorkflowGraphNode (`main` slot).

    The manifest's `class:` entry point resolves to this class
    (`src.graph.graph.RetailEarningsCallIRSummarizationAgent`), and
    src/api/server.py imports the same name.
    """

    @property
    def name(self) -> str:
        return "RetailEarningsCallIRSummarizationAgent"

    @property
    def state_schema(self) -> type:
        return State

    def register_nodes(self) -> None:
        super().register_nodes()  # injects InitializeNode + FinalizeNode (required)
        self._nodes["pre_process"] = PreProcessNode()
        self._nodes["main"] = IRSummarizationWorkflowGraphNode()
        self._nodes["post_process"] = PostProcessNode()

    # add_edges() is NOT overridden — backbone wiring is the framework's concern.


# Alias for backward-compatible server.py import (src.graph.graph.Graph)
Graph = RetailEarningsCallIRSummarizationAgent
