# The extraction grammar, pinned to expected VALUES rather than to "a value was found".
#
# Every metric this agent reports was previously the LAST DIGIT of the figure the
# document stated, with the sign dropped: +4.8% was reported as 8, 32.5% as 5,
# 8.4 turns as 4, ¥12bn as 0, and a ¥450bn full-year target as 0. The cause is a
# greedy gap between the metric label and its value — the engine consumes the
# line, then backtracks only far enough for the value group to match, which lands
# on the final digit.
#
# The shipped tests could not see it. They asserted a metric was present, or that
# the output parsed as JSON. A test that names the number the document states is
# the only kind that fails on this.

from __future__ import annotations

import pytest

from src.nodes.management_guidance_extract_node import _extract_guidance_highlights
from src.nodes.metrics_extract_node import _extract_financial_metrics


@pytest.mark.parametrize(
    ("document", "field", "expected"),
    [
        ("既存店売上高は前年同期比 +4.8% となりました。", "same_store_sales_pct", "+4.8"),
        ("既存店売上高は前年同期比 -2.5% でした。", "same_store_sales_pct", "-2.5"),
        ("同一店舗売上は 0.9% 増でした。", "same_store_sales_pct", "0.9"),
        ("Same-store sales grew 12.3% year on year.", "same_store_sales_pct", "12.3"),
        ("Comparable store sales declined -0.4% in the quarter.", "same_store_sales_pct", "-0.4"),
        ("粗利益率は 32.5% と前年から改善しています。", "gross_margin_delta_pct", "32.5"),
        ("粗利益率は前年比 +0.8pp 改善しました。", "gross_margin_delta_pct", "+0.8"),
        ("Gross margin improved 1.4pp year on year.", "gross_margin_delta_pct", "1.4"),
        ("在庫回転率は 8.4 回でした。", "inventory_turns", "8.4"),
        ("在庫回転率は 12.4回（前年 11.8回）", "inventory_turns", "12.4"),
        ("Inventory turns reached 11.9 times.", "inventory_turns", "11.9"),
        ("AI/DX 投資として 120 億円を計上しました。", "ai_dx_investments_jpy", "120"),
        ("AI・DX投資: 5,000百万円（前年度比2倍）", "ai_dx_investments_jpy", "5000"),
        ("DX investment of 1,250 million yen this year.", "ai_dx_investments_jpy", "1250"),
    ],
)
def test_the_extracted_value_is_the_stated_value(document: str, field: str, expected: str) -> None:
    assert _extract_financial_metrics(document)[field] == expected


@pytest.mark.parametrize("stated", ["1.1", "9.9", "41.8", "100.0"])
def test_different_documents_give_different_numbers(stated: str) -> None:
    """The reported figure follows the document across its whole range.

    Under the greedy grammar every one of these reported a single digit, so the
    number appeared to move with the input while being wrong each time.
    """
    document = f"既存店売上高は前年同期比 +{stated}% となりました。"
    assert _extract_financial_metrics(document)["same_store_sales_pct"] == f"+{stated}"


def test_an_absent_metric_is_reported_as_absent() -> None:
    metrics = _extract_financial_metrics("1. 今後の取り組み\n店舗網の再編を進めます。")
    assert all(value is None for value in metrics.values())


@pytest.mark.parametrize(
    "document",
    [
        "既存店売上高は前年同期比 99999% となりました。",
        "在庫回転率は 999999 回でした。",
    ],
)
def test_an_out_of_range_figure_is_not_reported(document: str) -> None:
    """A document is caller text, so an arbitrarily large digit run is a possible
    input for every numeric field. It is reported as absent, never rendered."""
    metrics = _extract_financial_metrics(document)
    assert all(value is None for value in metrics.values())


# ---------------------------------------------------------------------------
# Full-year targets, in the guidance block
# ---------------------------------------------------------------------------


def test_a_full_year_target_is_the_stated_figure() -> None:
    guidance = _extract_guidance_highlights("代表取締役社長は「通期 4500 億円の売上目標は据え置く」と述べました。")
    assert "Full-year targets stated: 4500" in guidance


def test_an_english_full_year_target_is_the_stated_figure() -> None:
    guidance = _extract_guidance_highlights("FY2026 revenue target of 1,200 billion yen.")
    assert "Full-year targets stated: 1200" in guidance


def test_executive_commentary_is_captured() -> None:
    """The attribution window has to span a quoted sentence.

    Japanese attribution puts the quotation between the title and the speech
    verb, so a short window matched nothing and executive commentary never
    appeared in the report.
    """
    guidance = _extract_guidance_highlights("代表取締役社長は「通期 4500 億円の売上目標は据え置く」と述べました。")
    assert "代表取締役社長" in guidance


def test_a_document_without_guidance_says_so() -> None:
    guidance = _extract_guidance_highlights("既存店売上高は前年同期比 +4.8% となりました。")
    assert guidance == "No explicit management guidance statements identified in document."


def test_the_guidance_block_is_bounded() -> None:
    """Bulk source text cannot ride out through the guidance block."""
    document = "\n".join(f"{i}. 今後の取り組み " + ("機密" * 300) for i in range(1, 40))
    guidance = _extract_guidance_highlights(document)
    assert len(guidance) <= 1_200
