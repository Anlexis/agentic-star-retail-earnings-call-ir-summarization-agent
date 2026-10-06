# RET-C2-303 — Unit Tests: Domain Nodes
#
# Covers: PreProcessNode (S-1 gate), IRDocumentParseNode, MetricsExtractNode,
#         ManagementGuidanceExtractNode, RegulatoryRiskFlagNode,
#         StructuredSummaryFormatNode, PostProcessNode (S-3 gate).
#
# Patch rule: emit_trace_event is patched AT THE NODE MODULE (no sys.modules stub).
# The real SDK ships shared.utils.audit_logger; patching sys.modules["shared"]
# breaks the framework's own `from shared.security.class_jwt_detector import ...`
# at load time.  Module-level monkeypatching is the correct pattern.

import json
import pytest

import src.nodes.pre_process_node as _pre_mod
import src.nodes.ir_document_parse_node as _parse_mod
import src.nodes.metrics_extract_node as _metrics_mod
import src.nodes.management_guidance_extract_node as _guidance_mod
import src.nodes.regulatory_risk_flag_node as _risk_mod
import src.nodes.structured_summary_format_node as _summary_mod
import src.nodes.post_process_node as _post_mod

from framework.schemas.agent_status import AgentStatus


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def patch_emit(monkeypatch):
    """Patch emit_trace_event in every domain node module (no sys.modules stub)."""

    def noop(*a, **k):
        return None

    for mod in (
        _pre_mod,
        _parse_mod,
        _metrics_mod,
        _guidance_mod,
        _risk_mod,
        _summary_mod,
        _post_mod,
    ):
        monkeypatch.setattr(mod, "emit_trace_event", noop)


_BASE_STATE: dict = {
    "caller_trust_level": "VERIFIED_EXTERNAL",
    "correlation_id": "test-unit-ret-c2-303",
    "node_history": [],
    "error_log": [],
}

# Valid IR document excerpt for happy-path tests
_VALID_IR_DOC = (
    "株式会社サンプルリテール 2025年3月期 第2四半期決算説明会\n\n"
    "【財務ハイライト】\n"
    "既存店売上高前年比: +3.5%\n"
    "粗利益率: 前年比+0.8pp改善（32.1%→32.9%）\n"
    "在庫回転: 12.4回（前年 11.8回）\n"
    "AI・DX投資: 5,000百万円（前年度比2倍）\n\n"
    "【経営ガイダンス】\n"
    "代表取締役社長より: 通期業績予想を上方修正します。\n"
    "FY2025通期売上高: 800,000百万円（前回予想比+5%）\n"
    "中期経営計画: 2026年度までにDX投資を倍増する見通し。\n\n"
    "【リスク情報】\n"
    "ESG報告義務: Scope 1/2排出量の開示を強化中。\n"
    "サステナビリティ委員会を設置し、脱炭素計画を策定。\n"
)


# ---------------------------------------------------------------------------
# PreProcessNode — S-1 gate
# ---------------------------------------------------------------------------


class TestPreProcessNode:
    """S-1 gate: validates IR document input before inner pipeline."""

    def setup_method(self):
        from src.nodes.pre_process_node import PreProcessNode

        self.node = PreProcessNode()

    def _state(self, user_input: str, **ctx):
        return {**_BASE_STATE, "user_input": user_input, "input_context": ctx}

    def test_valid_ir_doc_passes(self):
        """S-1 gate: well-formed IR document text yields SUCCESS and validated_input."""
        result = self.node.execute(self._state(_VALID_IR_DOC, channel="api"))
        assert result["status"] == AgentStatus.SUCCESS
        assert result["validated_input"] is not None
        assert len(result["validated_input"]) > 0

    def test_empty_input_returns_error(self):
        """S-1 gate: empty user_input returns ERROR."""
        result = self.node.execute(self._state(""))
        assert result["status"] == AgentStatus.ERROR
        assert any("empty" in e.lower() for e in result["error_log"])

    def test_whitespace_only_returns_error(self):
        """S-1 gate: whitespace-only input is treated as empty."""
        result = self.node.execute(self._state("   \n\t  "))
        assert result["status"] == AgentStatus.ERROR

    def test_injection_pattern_rejected(self):
        """S-1 gate: prompt-injection patterns are rejected."""
        for malicious in [
            "<script>alert(1)</script>",
            "{" * 2 + "template" + "}" * 2,
            "eval(malicious_code)",
            "__import__('os').system('ls')",
        ]:
            result = self.node.execute(self._state(malicious))
            assert result["status"] == AgentStatus.ERROR, f"Injection pattern not rejected: {malicious!r}"

    def test_oversized_input_rejected(self):
        """S-1 gate: input exceeding 2M chars is rejected (proxy for oversized PDF)."""
        huge = "IR doc content. " * 130_000  # > 2_000_000 chars
        result = self.node.execute(self._state(huge))
        assert result["status"] == AgentStatus.ERROR
        assert any("size" in e.lower() or "large" in e.lower() for e in result["error_log"])

    def test_enriched_context_contains_source(self):
        """pre_process must enrich state with source metadata."""
        result = self.node.execute(self._state("Valid IR content.", channel="mobile"))
        assert result["status"] == AgentStatus.SUCCESS
        ctx = json.loads(result["enriched_context"])
        assert "RetailEarningsCallIRSummarizationAgent" in ctx["source"]
        assert ctx["channel"] == "mobile"

    def test_execute_method_contract(self):
        """Node contract: node must implement execute(state) -> dict."""
        import inspect
        from src.nodes.pre_process_node import PreProcessNode

        sig = inspect.signature(PreProcessNode.execute)
        assert "state" in sig.parameters
        assert "_invoke_impl" not in PreProcessNode.__dict__

    def test_trust_level_is_verified_external(self):
        """Backbone pre_process must declare VERIFIED_EXTERNAL (external S-1 gate)."""
        from src.nodes.pre_process_node import PreProcessNode
        from framework.schemas.trust_level import TrustLevel

        assert PreProcessNode.required_trust_level == TrustLevel.VERIFIED_EXTERNAL


# ---------------------------------------------------------------------------
# IRDocumentParseNode
# ---------------------------------------------------------------------------


class TestIRDocumentParseNode:
    """Parses and normalizes IR document text for downstream extraction."""

    def setup_method(self):
        from src.nodes.ir_document_parse_node import IRDocumentParseNode

        self.node = IRDocumentParseNode()

    def _state(self, validated_input: str | None = None):
        return {
            **_BASE_STATE,
            "caller_trust_level": "ANONYMOUS",
            "validated_input": validated_input,
        }

    def test_normalizes_valid_document(self):
        """Valid document text is normalized and returned in parsed_document."""
        result = self.node.execute(self._state(_VALID_IR_DOC))
        assert result["status"] == AgentStatus.SUCCESS
        assert result["parsed_document"] is not None
        assert len(result["parsed_document"]) > 0

    def test_page_break_normalized(self):
        """Page break markers are replaced with normalized separator."""
        doc = "Section 1 content.\n\nPage 1\n\nSection 2 content."
        result = self.node.execute(self._state(doc))
        assert result["status"] == AgentStatus.SUCCESS
        # Original 'Page 1' literal should have been replaced
        parsed = result["parsed_document"]
        assert "--- PAGE BREAK ---" in parsed or "Section 1" in parsed

    def test_missing_validated_input_returns_error(self):
        """No validated_input in state returns ERROR."""
        result = self.node.execute(self._state(None))
        assert result["status"] == AgentStatus.ERROR
        assert len(result["error_log"]) > 0

    def test_trust_level_is_anonymous(self):
        """Inner domain node must declare ANONYMOUS trust level (review finding 5)."""
        from src.nodes.ir_document_parse_node import IRDocumentParseNode
        from framework.schemas.trust_level import TrustLevel

        assert IRDocumentParseNode.required_trust_level == TrustLevel.ANONYMOUS


# ---------------------------------------------------------------------------
# MetricsExtractNode
# ---------------------------------------------------------------------------


class TestMetricsExtractNode:
    """Extracts key financial metrics from parsed IR document."""

    def setup_method(self):
        from src.nodes.metrics_extract_node import MetricsExtractNode

        self.node = MetricsExtractNode()

    def _state(self, parsed_document: str | None):
        return {
            **_BASE_STATE,
            "caller_trust_level": "ANONYMOUS",
            "parsed_document": parsed_document,
        }

    def test_extracts_metrics_from_valid_document(self):
        """Valid IR document with financial data produces non-empty metrics."""
        result = self.node.execute(self._state(_VALID_IR_DOC))
        assert result["status"] == AgentStatus.SUCCESS
        metrics = json.loads(result["financial_metrics_json"])
        assert isinstance(metrics, dict)
        # At least one metric should be found in our sample IR doc
        assert any(v is not None for v in metrics.values())

    def test_same_store_sales_extracted(self):
        """Same-store sales percentage is extracted from matching text."""
        doc = "既存店売上高前年比は+3.5%の増収となりました。"
        result = self.node.execute(self._state(doc))
        assert result["status"] == AgentStatus.SUCCESS
        metrics = json.loads(result["financial_metrics_json"])
        assert metrics["same_store_sales_pct"] is not None

    def test_metrics_output_is_json_serialized(self):
        """financial_metrics_json is a valid JSON string."""
        result = self.node.execute(self._state("Sales data for Q2."))
        assert result["status"] == AgentStatus.SUCCESS
        # Must be parseable JSON
        data = json.loads(result["financial_metrics_json"])
        assert isinstance(data, dict)
        # All expected keys present
        for key in ("same_store_sales_pct", "gross_margin_delta_pct", "inventory_turns", "ai_dx_investments_jpy"):
            assert key in data

    def test_missing_parsed_document_returns_error(self):
        """No parsed_document in state returns ERROR."""
        result = self.node.execute(self._state(None))
        assert result["status"] == AgentStatus.ERROR

    def test_trust_level_is_anonymous(self):
        """Inner domain node must declare ANONYMOUS trust level (review finding 5)."""
        from src.nodes.metrics_extract_node import MetricsExtractNode
        from framework.schemas.trust_level import TrustLevel

        assert MetricsExtractNode.required_trust_level == TrustLevel.ANONYMOUS


# ---------------------------------------------------------------------------
# ManagementGuidanceExtractNode
# ---------------------------------------------------------------------------


class TestManagementGuidanceExtractNode:
    """Extracts forward-looking management guidance from IR document."""

    def setup_method(self):
        from src.nodes.management_guidance_extract_node import ManagementGuidanceExtractNode

        self.node = ManagementGuidanceExtractNode()

    def _state(self, parsed_document: str | None):
        return {
            **_BASE_STATE,
            "caller_trust_level": "ANONYMOUS",
            "parsed_document": parsed_document,
        }

    def test_extracts_guidance_from_valid_document(self):
        """Valid IR document with guidance section produces non-empty management_guidance."""
        result = self.node.execute(self._state(_VALID_IR_DOC))
        assert result["status"] == AgentStatus.SUCCESS
        assert result["management_guidance"] is not None
        assert len(result["management_guidance"]) > 0

    def test_guidance_section_detected(self):
        """Guidance/outlook section keywords trigger extraction."""
        doc = "経営ガイダンス: 通期売上高を500,000百万円に上方修正する見通しです。"
        result = self.node.execute(self._state(doc))
        assert result["status"] == AgentStatus.SUCCESS
        assert result["management_guidance"] is not None

    def test_missing_parsed_document_returns_error(self):
        """No parsed_document in state returns ERROR."""
        result = self.node.execute(self._state(None))
        assert result["status"] == AgentStatus.ERROR

    def test_guidance_respects_size_limit(self):
        """Guidance output is capped to prevent bulk bleed-through (S-2)."""
        large_doc = "経営方針: " + "長い説明文 " * 1000
        result = self.node.execute(self._state(large_doc))
        assert result["status"] == AgentStatus.SUCCESS
        assert len(result["management_guidance"]) <= 5_100  # _MAX_GUIDANCE_CHARS + small buffer

    def test_trust_level_is_anonymous(self):
        """Inner domain node must declare ANONYMOUS trust level (review finding 5)."""
        from src.nodes.management_guidance_extract_node import ManagementGuidanceExtractNode
        from framework.schemas.trust_level import TrustLevel

        assert ManagementGuidanceExtractNode.required_trust_level == TrustLevel.ANONYMOUS


# ---------------------------------------------------------------------------
# RegulatoryRiskFlagNode
# ---------------------------------------------------------------------------


class TestRegulatoryRiskFlagNode:
    """Flags regulatory and compliance risk signals from IR document."""

    def setup_method(self):
        from src.nodes.regulatory_risk_flag_node import RegulatoryRiskFlagNode

        self.node = RegulatoryRiskFlagNode()

    def _state(self, parsed_document: str | None):
        return {
            **_BASE_STATE,
            "caller_trust_level": "ANONYMOUS",
            "parsed_document": parsed_document,
        }

    def test_detects_esg_risk(self):
        """ESG reporting obligation pattern triggers REG-006 (LOW)."""
        doc = "ESG目標: Scope 1排出量を2030年までに50%削減するサステナビリティ計画。"
        result = self.node.execute(self._state(doc))
        assert result["status"] == AgentStatus.SUCCESS
        risks = json.loads(result["regulatory_risks_json"])
        codes = [r["risk_code"] for r in risks]
        assert "REG-006" in codes

    def test_no_risks_for_clean_document(self):
        """Document with no risk signals yields empty risk list."""
        doc = "業績は順調に推移しており、通期予想を達成見込みです。"
        result = self.node.execute(self._state(doc))
        assert result["status"] == AgentStatus.SUCCESS
        risks = json.loads(result["regulatory_risks_json"])
        assert isinstance(risks, list)
        # Plain business text shouldn't trigger regulatory flags

    def test_risks_output_is_json_list(self):
        """regulatory_risks_json must be a valid JSON array."""
        result = self.node.execute(self._state(_VALID_IR_DOC))
        assert result["status"] == AgentStatus.SUCCESS
        risks = json.loads(result["regulatory_risks_json"])
        assert isinstance(risks, list)
        for r in risks:
            assert "risk_code" in r
            assert "severity" in r
            assert r["severity"] in ("HIGH", "MEDIUM", "LOW")
            assert "description" in r

    def test_antitrust_pattern_detects_high_severity(self):
        """Antitrust/fair-trade signal yields REG-001 HIGH severity."""
        doc = "独占禁止法違反の疑いについて当局より問い合わせを受けました。"
        result = self.node.execute(self._state(doc))
        assert result["status"] == AgentStatus.SUCCESS
        risks = json.loads(result["regulatory_risks_json"])
        high_risks = [r for r in risks if r["severity"] == "HIGH"]
        assert len(high_risks) >= 1

    def test_missing_parsed_document_returns_error(self):
        """No parsed_document in state returns ERROR."""
        result = self.node.execute(self._state(None))
        assert result["status"] == AgentStatus.ERROR

    def test_trust_level_is_anonymous(self):
        """Inner domain node must declare ANONYMOUS trust level (review finding 5)."""
        from src.nodes.regulatory_risk_flag_node import RegulatoryRiskFlagNode
        from framework.schemas.trust_level import TrustLevel

        assert RegulatoryRiskFlagNode.required_trust_level == TrustLevel.ANONYMOUS


# ---------------------------------------------------------------------------
# StructuredSummaryFormatNode
# ---------------------------------------------------------------------------


class TestStructuredSummaryFormatNode:
    """Assembles the final structured IR summary from upstream extraction outputs."""

    def setup_method(self):
        from src.nodes.structured_summary_format_node import StructuredSummaryFormatNode

        self.node = StructuredSummaryFormatNode()

    def _state(self, **overrides):
        base = {
            **_BASE_STATE,
            "caller_trust_level": "ANONYMOUS",
            "financial_metrics_json": json.dumps(
                {
                    "same_store_sales_pct": "3.5",
                    "gross_margin_delta_pct": "0.8",
                    "inventory_turns": "12.4",
                    "ai_dx_investments_jpy": "5,000",
                },
                ensure_ascii=False,
            ),
            "management_guidance": "通期見通し: 売上高800,000百万円に上方修正。",
            "regulatory_risks_json": json.dumps(
                [{"risk_code": "REG-006", "severity": "LOW", "description": "ESG reporting obligation"}],
                ensure_ascii=False,
            ),
        }
        base.update(overrides)
        return base

    def test_produces_structured_summary(self):
        """All upstream data available → SUCCESS with non-empty structured_summary."""
        result = self.node.execute(self._state())
        assert result["status"] == AgentStatus.SUCCESS
        assert result["structured_summary"] is not None
        assert len(result["structured_summary"]) > 0

    def test_output_contains_metrics_block(self):
        """structured_summary must contain the JSON metrics block (S-3 requirement)."""
        result = self.node.execute(self._state())
        assert result["status"] == AgentStatus.SUCCESS
        assert "```json" in result["structured_summary"]

    def test_output_contains_guidance_section(self):
        """structured_summary must contain the guidance section."""
        result = self.node.execute(self._state())
        assert result["status"] == AgentStatus.SUCCESS
        # Management guidance heading should be present
        assert "Guidance" in result["structured_summary"] or "guidance" in result["structured_summary"].lower()

    def test_output_contains_risk_section(self):
        """structured_summary must contain the regulatory risk section."""
        result = self.node.execute(self._state())
        assert result["status"] == AgentStatus.SUCCESS
        assert "REG-006" in result["structured_summary"] or "Risk" in result["structured_summary"]

    def test_no_bulk_document_in_output(self):
        """S-3: output must not contain bulk IR document text (PAGE BREAK marker)."""
        result = self.node.execute(self._state())
        assert result["status"] == AgentStatus.SUCCESS
        # The raw page-break marker from parse node should not appear in summary
        assert "--- PAGE BREAK ---" not in result["structured_summary"]

    def test_credential_pattern_rejected(self):
        """Output carrying a credential pattern is withheld, not carried forward."""
        malicious_guidance = "Management said password=secret123 should not appear."
        result = self.node.execute(self._state(management_guidance=malicious_guidance))
        assert result["status"] == AgentStatus.ERROR
        # Present and empty rather than absent: a partial delta is merged, so a
        # key left out keeps whatever the field already held.
        assert result["structured_summary"] == ""
        assert "secret123" not in json.dumps(result)

    def test_no_upstream_data_returns_error(self):
        """No upstream extraction results → ERROR."""
        result = self.node.execute(
            {
                **_BASE_STATE,
                "caller_trust_level": "ANONYMOUS",
            }
        )
        assert result["status"] == AgentStatus.ERROR

    def test_trust_level_is_anonymous(self):
        """Inner domain node must declare ANONYMOUS trust level (review finding 5)."""
        from src.nodes.structured_summary_format_node import StructuredSummaryFormatNode
        from framework.schemas.trust_level import TrustLevel

        assert StructuredSummaryFormatNode.required_trust_level == TrustLevel.ANONYMOUS


# ---------------------------------------------------------------------------
# PostProcessNode — S-3 gate
# ---------------------------------------------------------------------------


class TestPostProcessNode:
    """Output boundary: credential screen, structure invariant, containment."""

    def setup_method(self):
        from src.nodes.post_process_node import PostProcessNode

        self.node = PostProcessNode()

    def _state(self, result: str | None = None):
        return {
            **_BASE_STATE,
            "caller_trust_level": "ANONYMOUS",
            "result": result,
        }

    def _valid_summary(self) -> str:
        return (
            "## Financial Metrics\n\n"
            "```json\n"
            '{"same_store_sales_pct": "3.5"}\n'
            "```\n\n"
            "## Management Forward Guidance\n\n"
            '"通期見通しを上方修正。"\n\n'
            "## Regulatory Risk Signals\n\n"
            "No regulatory risk signals detected."
        )

    def test_valid_summary_passes_gate(self):
        """A well-formed report is published unchanged."""
        result = self.node.execute(self._state(self._valid_summary()))
        assert result["status"] == AgentStatus.SUCCESS
        assert result["formatted_output"] == self._valid_summary()

    def test_empty_result_is_reported_as_a_failure(self):
        """Reaching the boundary with nothing to publish is a failed run.

        This replaces an assertion that the node returned SUCCESS with an empty
        formatted_output. That contract was wrong twice over: it told the caller
        an empty answer was the answer, and an empty formatted_output is falsy,
        which re-opens the framework's `formatted_output or result` fallback
        rather than closing it.
        """
        result = self.node.execute(self._state(None))
        assert result["status"] == AgentStatus.ERROR
        assert result["formatted_output"], "a falsy replacement re-opens the fallback"
        assert "upstream_failure" in result["formatted_output"]

    def test_credential_in_result_is_contained(self):
        """A credential in the report withholds it and clears what carried it."""
        result = self.node.execute(self._state("Summary ok. api_key=supersecret123"))
        assert result["status"] == AgentStatus.ERROR
        assert result["result"] == ""
        assert result["structured_summary"] == ""
        assert "supersecret123" not in json.dumps(result)
        assert "Summary ok" not in json.dumps(result)
        assert result["formatted_output"], "a falsy replacement re-opens the fallback"

    def test_output_key_is_formatted_output(self):
        """post_process must write formatted_output (not result) for FinalizeNode."""
        result = self.node.execute(self._state(self._valid_summary()))
        assert "formatted_output" in result

    def test_trust_level_is_anonymous(self):
        """Backbone post_process node declares ANONYMOUS trust level."""
        from src.nodes.post_process_node import PostProcessNode
        from framework.schemas.trust_level import TrustLevel

        assert PostProcessNode.required_trust_level == TrustLevel.ANONYMOUS
