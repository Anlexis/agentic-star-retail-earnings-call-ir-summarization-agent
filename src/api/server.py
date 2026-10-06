"""AgentCore Platform v1.0"""

# Standalone HTTP entry point for the agent.
# Entry points are adapters only — no business logic here.
# For platform-level routing, the gateway calls agent.invoke() directly.

import os
import secrets
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request
from framework.schemas.invocation_context import InvocationContext
from framework.schemas.trust_level import TrustLevel
from framework.secrets.context import bound_secrets
from pydantic import BaseModel, Field
from shared.secrets import factory as secrets_factory

from src.graph.graph import RetailEarningsCallIRSummarizationAgent
from src.services import screens
from src.services.runtime_config import runtime_config

app = FastAPI(title="Agent")

# The registry loads config/config.yaml itself and passes it as Graph(config=...).
# Constructing the graph bare here would leave every declared runtime value inert
# on this deployment while the registry deployment honoured it — the same agent
# behaving differently depending on how it was started.
_config = runtime_config()

agent = RetailEarningsCallIRSummarizationAgent(config=_config)
agent.compile()

# The secret provider is scoped by the identity the manifest declares. The
# provider reads `env/namespaces/{namespace}/…` and
# `env/agents/{namespace}/{agent_name}/…`, and a missing tier file is ignored by
# design — so a namespace that disagrees with config/agent.yaml has no boot-time
# symptom and silently splits one agent's secrets across two stores the first
# time a secret is declared. `namespace` is lower(industry) — "ret" — not the
# lowercased template id. tests/integration/test_manifest_identity_alignment.py
# holds both values to the manifest by reading them rather than restating them.
agent.provision_secrets(secrets_factory(namespace="ret", agent_name="RetailEarningsCallIRSummarizationAgent"))


class InvokeRequest(BaseModel):
    input: str
    session_id: str = ""
    input_context: dict[str, Any] = Field(default_factory=dict)


def _bearer_matches(supplied: str, expected: str) -> bool:
    """Constant-time bearer comparison that is safe for non-ASCII header input."""
    return secrets.compare_digest(supplied.encode(), f"Bearer {expected}".encode())


def _resolve_standalone_trust(
    current: TrustLevel,
    authorization: str,
    invoke_auth_token: str | None,
    internal_runner_token: str | None,
) -> TrustLevel:
    """Authenticate standalone callers without allowing external-token elevation.

    Without this the caller is always ANONYMOUS, the entry node requires
    VERIFIED_EXTERNAL, and every request is refused at the trust gate before any
    document is read — the agent answers, but it can never answer anything.

    The internal runner token is a distinct deployment credential: it is
    considered only for an anonymous caller and maps exactly to INTERNAL, while
    the ordinary invoke token maps to VERIFIED_EXTERNAL. Trust already
    established by middleware is never changed.
    """
    if current is not TrustLevel.ANONYMOUS:
        return current
    if internal_runner_token and _bearer_matches(authorization, internal_runner_token):
        return TrustLevel.INTERNAL
    if invoke_auth_token and _bearer_matches(authorization, invoke_auth_token):
        return TrustLevel.VERIFIED_EXTERNAL
    if internal_runner_token or invoke_auth_token:
        raise HTTPException(status_code=401, detail="Token is invalid or expired.")
    return TrustLevel.ANONYMOUS


def _reject(field: str, reason: str) -> HTTPException:
    """A refusal that names the field and a closed-set reason, never the value.

    400 rather than 422: pydantic owns 422 and answers there with a list of
    error objects, so reusing it would make client handling ambiguous.
    """
    return HTTPException(status_code=400, detail={"field": field, "reason": reason})


def _screen_request(req: InvokeRequest) -> dict[str, str]:
    """Validate the caller payload and return the context the agent will see.

    A credential-shaped string is refused here rather than left to fail inside
    the graph. The first framework node returns its inputs verbatim into its own
    result, and the framework's output gate scans every value of every result —
    so such a request dies at node one with a traceback the caller cannot act
    on. It cannot succeed either way; a refusal naming the field is the useful
    form of the same answer.
    """
    if not req.input or not req.input.strip():
        raise _reject("input", screens.REASON_EMPTY_INPUT)
    if len(req.input) > screens.MAX_INPUT_SIZE_CHARS:
        raise _reject("input", screens.REASON_INPUT_TOO_LARGE)
    if screens.detect_credential_labels(req.input):
        raise _reject("input", screens.REASON_CREDENTIAL_SHAPE)

    raw_context = req.input_context or {}
    if screens.detect_credential_labels(raw_context):
        raise _reject("input_context", screens.REASON_CREDENTIAL_SHAPE)
    reason = screens.screen_structure(raw_context)
    if reason:
        raise _reject("input_context", reason)

    # A supported key whose value is not inert is REFUSED rather than dropped.
    # Dropping it would answer a different request than the caller made, and
    # would leave the entry node's own validation of these fields unreachable.
    for key in screens.SUPPORTED_CONTEXT_KEYS:
        value = raw_context.get(key)
        if value is None:
            continue
        if not isinstance(value, str) or not screens.is_inert_identifier(value.strip().lower()):
            raise _reject(f"input_context.{key}", screens.REASON_UNSUPPORTED_FIELD_VALUE)

    # Keys this agent does not consume are dropped, not forwarded: an ignored
    # key still travels on the context channel and reaches the framework's own
    # result scan.
    return screens.normalise_context(raw_context)


@app.post("/invoke")
async def invoke(req: InvokeRequest, request: Request) -> Any:
    # This adapter is the entry-point auth boundary. Both values are
    # deployment-level caller credentials, not agent secrets: no invocation
    # context exists before this boundary, so per-request secret binding cannot
    # apply to them.
    trust = _resolve_standalone_trust(
        getattr(request.state, "trust_level", TrustLevel.ANONYMOUS),
        request.headers.get("authorization", ""),
        os.environ.get("INVOKE_AUTH_TOKEN"),
        os.environ.get("STG_INTERNAL_RUNNER_TOKEN"),
    )
    context = _screen_request(req)
    with bound_secrets(agent._secrets_provider):
        ctx = InvocationContext(
            session_id=req.session_id or str(uuid4()),
            caller_trust_level=trust,
            caller_id=getattr(request.state, "caller_id", ""),
        )
        return agent.invoke(req.input, ctx=ctx, input_context=context)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "agent": "RetailEarningsCallIRSummarizationAgent"}
