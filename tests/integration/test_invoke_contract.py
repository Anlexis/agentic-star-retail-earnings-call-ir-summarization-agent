# End-to-end through the real ASGI entry point, at the trust level the manifest
# declares.
#
# Every assertion here drives the compiled graph through POST /invoke with a
# bearer credential, because that is the only path a deployed caller has. A unit
# test of a node cannot tell whether the deployed agent can answer at all: this
# template refused every request it ever received — nothing established the
# caller's trust level, so the entry node's VERIFIED_EXTERNAL requirement was
# never satisfiable and the agent returned `status: error` for all input.

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

_REPO_ROOT = Path(__file__).resolve().parents[2]
_PAYLOAD_PATH = _REPO_ROOT / "deploy" / "invoke_payload.json"

_TOKEN = "test-invoke-token"

# The document the deployed smoke check posts. Defined here, in the repository's
# own fixture, and asserted equal to deploy/invoke_payload.json below — so the
# payload and the tests assert the same contract and cannot drift apart.
_VALID_PAYLOAD: str = (
    "株式会社サンプルリテール 2026年3月期 第2四半期 決算説明会\n"
    "\n"
    "1. 財務ハイライト\n"
    "既存店売上高は前年同期比 +4.8% となりました。\n"
    "粗利益率は 32.5% と前年から改善しています。\n"
    "在庫回転率は 8.4 回でした。\n"
    "AI/DX 投資として 120 億円を計上しました。\n"
    "\n"
    "2. 今後の取り組み\n"
    "中期経営計画に基づき、店舗網の再編を進めてまいります。\n"
    "代表取締役社長は「通期 4500 億円の売上目標は据え置く」と述べました。\n"
    "\n"
    "3. リスク情報\n"
    "景品表示法に関する表示適正化の取り組みを継続します。\n"
    "物流2024年問題への対応としてドライバー不足の解消を図ります。\n"
)


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """A client whose bearer the adapter accepts as VERIFIED_EXTERNAL."""
    monkeypatch.setenv("INVOKE_AUTH_TOKEN", _TOKEN)
    from src.api.server import app

    return TestClient(app)


def _post(client: TestClient, body: dict, token: str | None = _TOKEN):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return client.post("/invoke", json=body, headers=headers)


# ---------------------------------------------------------------------------
# The deployed smoke payload
# ---------------------------------------------------------------------------


def test_deploy_payload_matches_the_repository_fixture() -> None:
    """deploy/invoke_payload.json carries this module's own fixture.

    The deployed smoke check posts that file verbatim. Taking its value from the
    fixture the tests assert against is what stops the two describing different
    contracts — a payload invented for the deploy can be refused by the entry
    node while every surrounding HTTP assertion still passes.
    """
    payload = json.loads(_PAYLOAD_PATH.read_text(encoding="utf-8"))
    assert payload["input"] == _VALID_PAYLOAD


def test_the_deploy_payload_is_answered(client: TestClient) -> None:
    payload = json.loads(_PAYLOAD_PATH.read_text(encoding="utf-8"))
    response = _post(client, payload)
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "success", body
    assert body["output"]


# ---------------------------------------------------------------------------
# The public path does real work
# ---------------------------------------------------------------------------


def test_the_report_states_the_documents_own_numbers(client: TestClient) -> None:
    """The figures in the report are the figures in the document.

    Each of these was previously reported as the LAST DIGIT of its own value —
    +4.8% as "8", 32.5% as "5", 8.4 turns as "4" and ¥12bn as "0" — so a test
    that only asserted a metric had been "found" passed throughout.
    """
    body = _post(client, {"input": _VALID_PAYLOAD}).json()
    output = body["output"]
    assert body["status"] == "success", body
    assert '"same_store_sales_pct": "+4.8"' in output
    assert '"gross_margin_delta_pct": "32.5"' in output
    assert '"inventory_turns": "8.4"' in output
    assert '"ai_dx_investments_jpy": "120"' in output
    assert "Full-year targets stated: 4500" in output


@pytest.mark.parametrize(
    ("stated", "expected"),
    [("+1.2", "+1.2"), ("-7.9", "-7.9"), ("41.8", "41.8")],
)
def test_the_reported_growth_follows_the_document(client: TestClient, stated: str, expected: str) -> None:
    """Two very different documents must not produce the same number."""
    document = f"1. 財務ハイライト\n既存店売上高は前年同期比 {stated}% となりました。\n"
    body = _post(client, {"input": document}).json()
    assert body["status"] == "success", body
    assert f'"same_store_sales_pct": "{expected}"' in body["output"]


def test_a_document_without_metrics_reports_them_as_absent(client: TestClient) -> None:
    document = "1. 今後の取り組み\n店舗網の再編を進めてまいります。\n"
    body = _post(client, {"input": document}).json()
    assert body["status"] == "success", body
    assert '"same_store_sales_pct": null' in body["output"]


def test_the_risk_table_reflects_the_document(client: TestClient) -> None:
    with_risk = _post(client, {"input": "1. リスク情報\n独占禁止法に関する調査に対応しています。\n"}).json()
    without_risk = _post(client, {"input": "1. 財務ハイライト\n既存店売上高は前年同期比 +1.0% となりました。\n"}).json()
    assert "REG-001" in with_risk["output"]
    assert "No regulatory risk signals detected." in without_risk["output"]


# ---------------------------------------------------------------------------
# The caller context contract
# ---------------------------------------------------------------------------


def test_declared_context_reaches_the_report(client: TestClient) -> None:
    """input_context crosses into the inner graph and is rendered.

    The framework invokes a subgraph without a context channel, so this is the
    end-to-end proof that the bridge carries it — a node-level test of the inner
    graph would pass with the bridge removed.
    """
    body = _post(
        client,
        {"input": _VALID_PAYLOAD, "input_context": {"channel": "ir_portal", "doc_language": "ja"}},
    ).json()
    assert body["status"] == "success", body
    assert "Source channel: ir_portal" in body["output"]
    assert "Document language: ja" in body["output"]


def test_absent_context_degrades_to_the_declared_default(client: TestClient) -> None:
    body = _post(client, {"input": _VALID_PAYLOAD}).json()
    assert body["status"] == "success", body
    assert "Source channel: unknown · Document language: auto" in body["output"]


def test_an_unsupported_context_key_is_dropped(client: TestClient) -> None:
    """An unknown key never reaches the graph.

    Ignoring a key is not dropping it: an ignored key still travels on the
    context channel, and the framework's first node returns that channel
    verbatim into its own result where the output gate scans it — so the request
    dies at node one rather than being answered.
    """
    response = _post(
        client,
        {"input": _VALID_PAYLOAD, "input_context": {"channel": "ir_portal", "operator_note": "x"}},
    )
    body = response.json()
    assert response.status_code == 200
    assert body["status"] == "success", body
    assert "operator_note" not in body["output"]


def test_free_text_on_the_context_channel_is_refused(client: TestClient) -> None:
    """A supported key with a non-inert value is refused, not silently dropped.

    Dropping it would answer a different request than the caller made — the
    report would state a channel the caller never asked for — and would leave
    the entry node's own validation of these fields unreachable in production.
    """
    response = _post(client, {"input": _VALID_PAYLOAD, "input_context": {"channel": "Ginza <b>flagship</b>"}})
    assert response.status_code == 400
    detail = response.json()["detail"]
    assert detail == {
        "field": "input_context.channel",
        "reason": "unsupported_field_value",
    }
    assert "Ginza" not in json.dumps(detail)


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------


def test_an_unauthenticated_caller_is_refused(client: TestClient) -> None:
    response = client.post("/invoke", json={"input": _VALID_PAYLOAD})
    assert response.status_code == 401


def test_a_wrong_bearer_is_refused(client: TestClient) -> None:
    response = _post(client, {"input": _VALID_PAYLOAD}, token="not-the-token")
    assert response.status_code == 401


def test_health_does_not_require_a_credential(client: TestClient) -> None:
    assert client.get("/health").status_code == 200


# ---------------------------------------------------------------------------
# Caller input bounds
# ---------------------------------------------------------------------------


def test_empty_input_is_refused_by_field_and_reason(client: TestClient) -> None:
    response = _post(client, {"input": "   "})
    assert response.status_code == 400
    detail = response.json()["detail"]
    assert detail == {"field": "input", "reason": "empty_input"}


def test_oversized_input_is_refused(client: TestClient) -> None:
    from src.services import screens

    response = _post(client, {"input": "あ" * (screens.MAX_INPUT_SIZE_CHARS + 1)})
    assert response.status_code == 400
    assert response.json()["detail"]["reason"] == "input_too_large"


@pytest.mark.parametrize(
    "credential",
    [
        "AKIAIOSFODNN7EXAMPLE",
        "sk-abcdefghijklmnopqrstuvwxyz",
        "Bearer abcdef0123456789abcdef",
        "postgresql://db.example.internal/appdb",
        "password=hunter2",
    ],
)
def test_a_credential_shaped_document_is_refused_readably(client: TestClient, credential: str) -> None:
    """A refusal naming the field, not an opaque failure inside the graph.

    The framework's first nodes return their inputs verbatim into their own
    results, and its output gate scans every value of every result — so a
    credential-shaped string anywhere in the document ends the run with a
    traceback in the error log before any of this template's code runs. The
    request cannot succeed either way; naming the field is the useful form of
    the same answer.
    """
    response = _post(client, {"input": f"{_VALID_PAYLOAD}\n参考: {credential}\n"})
    assert response.status_code == 400
    detail = response.json()["detail"]
    assert detail == {"field": "input", "reason": "credential_shape_detected"}
    assert credential not in json.dumps(detail)


def test_ordinary_domain_text_on_the_same_field_still_passes(client: TestClient) -> None:
    """The credential screen must not refuse real IR prose.

    Probed with sentences from this repository's own fixture rather than
    invented ones: a screen that blocks legitimate documents is the failure that
    stops real work.
    """
    body = _post(client, {"input": _VALID_PAYLOAD}).json()
    assert body["status"] == "success", body
