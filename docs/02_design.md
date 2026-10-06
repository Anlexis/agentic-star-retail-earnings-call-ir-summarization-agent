# Template Design Specification — RET-C2-303

## Position in AgentCore Architecture

- **Agent Class**: RetailEarningsCallIRSummarizationAgent
- **L1 Base**: AgentBaseGraph (Cat 2 — multi-step domain workflow, nested GraphNode pattern)
- **Three-Layer Separation**:
  - State: flat TypedDict composition (no Pydantic — msgpack incompatible)
  - Node: L1 inheritance (Template Method: `execute(self, state: dict) -> dict` override only)
  - Graph: composition (`register_nodes()` for node substitution)

## Architecture Overview

RET-C2-303 processes retail earnings call transcripts and IR documents to produce structured
financial intelligence reports. The agent follows the Cat 2 nested-graph pattern:

- **Outer backbone** (AgentBaseGraph, fixed): `initialize → pre_process → main → post_process → finalize`
- **Inner domain graph** (BaseGraph): `ir_document_parse → metrics_extract → management_guidance_extract → regulatory_risk_flag → structured_summary_format`

The `main` backbone slot holds `IRSummarizationWorkflowGraphNode` (a `GraphNode` subclass) which
delegates to the inner `IRSummarizationDomainGraph`.

### Node Configuration

| Node | Class | Slot | Responsibility | Trust Level |
|------|-------|------|---------------|-------------|
| initialize | InitializeNode (default) | initialize | Session setup, schema version, trust injection | — |
| pre_process | PreProcessNode | pre_process | S-1 input validation gate (size, content classes, context contract) | VERIFIED_EXTERNAL |
| main | IRSummarizationWorkflowGraphNode | main | GraphNode → delegates to inner domain graph | — |
| post_process | PostProcessNode | post_process | S-3 output boundary: credential screen, structure invariant, containment | ANONYMOUS |
| finalize | FinalizeNode (default) | finalize | Response metadata, timing | — |
| ir_document_parse | IRDocumentParseNode | inner | Parse + normalize IR document text | ANONYMOUS |
| metrics_extract | MetricsExtractNode | inner | Extract key financial metrics | ANONYMOUS |
| management_guidance_extract | ManagementGuidanceExtractNode | inner | Extract management forward guidance | ANONYMOUS |
| regulatory_risk_flag | RegulatoryRiskFlagNode | inner | Flag regulatory/compliance risk signals | ANONYMOUS |
| structured_summary_format | StructuredSummaryFormatNode | inner | Format structured IR summary output | ANONYMOUS |

### Data Flow

```
Outer backbone (AgentBaseGraph):
  START
    → initialize        (InitializeNode — trust injection, session_id)
    → pre_process       (PreProcessNode — S-1 gate: validate IR doc input)
    → main              (IRSummarizationWorkflowGraphNode — GraphNode)
        [Inner domain graph: IRSummarizationDomainGraph]
          START
            → ir_document_parse          (normalize doc text)
            → metrics_extract            (same-store sales, margin, AI/DX)
            → management_guidance_extract (CEO/CFO forward guidance)
            → regulatory_risk_flag       (regulatory / compliance signals)
            → structured_summary_format  (structured JSON + markdown output)
          END
    → post_process      (PostProcessNode — S-3 gate, output formatting)
    → finalize          (FinalizeNode — response metadata)
  END
         ↓ (RETRY, max 3)
       pre_process
```

### State Definition

| Field | Type | Populated By | Purpose |
|-------|------|-------------|---------|
| validated_input | Optional[str] | PreProcessNode | Sanitized IR document text (S-1 cleared) |
| enriched_context | Optional[str] | PreProcessNode | JSON: {source, channel, doc_language} |
| parsed_document | Optional[str] | IRDocumentParseNode | Normalized document text (page breaks cleaned) |
| financial_metrics_json | Optional[str] | MetricsExtractNode | JSON: {same_store_sales_pct, gross_margin_delta_pct, inventory_turns, ai_dx_investments_jpy} |
| management_guidance | Optional[str] | ManagementGuidanceExtractNode | Forward guidance highlights text |
| regulatory_risks_json | Optional[str] | RegulatoryRiskFlagNode | JSON list: [{risk_code, severity, description}] |
| structured_summary | Optional[str] | StructuredSummaryFormatNode | Final structured IR summary (JSON + markdown) |
| result | Optional[str] | IRSummarizationWorkflowGraphNode.merge_output | Mapped from structured_summary |
| formatted_output | Optional[str] | PostProcessNode | Final caller-facing output after S-3 gate |

**State Constraints (mandatory):**
- Flat TypedDict only (primitives + JSON-serializable types)
- No JWT, API keys, credentials in State (checkpoint DB leakage)
- InvocationContext via `config["configurable"]` only (not in State)
- No Pydantic models, dataclass, arbitrary Python objects (msgpack incompatible)
- Complex data (dict / list) serialized as `Optional[str]` via `json.dumps`/`json.loads` (ADR-005)

## Security Gate Design

### S-1 (Input Validation) — the entry adapter and PreProcessNode
- The standalone entry point is the authentication boundary: it maps
  `INVOKE_AUTH_TOKEN` to VERIFIED_EXTERNAL and `STG_INTERNAL_RUNNER_TOKEN` to INTERNAL,
  and never lets an external token elevate past trust already established upstream.
  Without it every caller is ANONYMOUS, the entry node's VERIFIED_EXTERNAL requirement
  is unsatisfiable, and the agent refuses every request it receives.
- Empty input rejection; input size limit (2,000,000 chars, a proxy for oversized documents)
- Script / template-execution marker scan (`{{`, `}}`, `<script`, `javascript:`, `eval(`,
  `__import__`, `os.system`)
- **Chat-template control markers screened as a class** — `<|…|>`, `[INST]`, `<<SYS>>`,
  `<s>`, `<system>` — raw and markup-stripped. Measured against the installed framework:
  it scores `<|im_start|>` and `[INST]` as high-confidence injections and returns nothing
  at all for `<<SYS>>`, so the class has to be screened here rather than delegated.
- **Credential shapes are screened at the adapter, before `invoke()`.** The framework's
  first nodes return their inputs verbatim into their own results and its output gate
  scans every value of every result, so a credential-shaped string anywhere in the
  document ends the run at the first node with a traceback in the error log. The request
  cannot succeed either way; the adapter converts it into a `400` naming the field.
- **Caller context contract**: `input_context` accepts `channel` and `doc_language` only.
  A supported key whose value is not an inert identifier (`[a-z0-9_-]{1,32}`) is refused;
  unsupported keys are DROPPED before `invoke()`, because an ignored key still travels on
  the context channel into the framework's own result scan. Both values render into the
  report, which is why they are locked to an inert alphabet.
- `required_trust_level = TrustLevel.VERIFIED_EXTERNAL`

### S-2 (Input Gate Hook)
- Framework `@final _security_gate_input()` runs automatically on `FunctionNode` subclasses
- Domain-level validation covered by S-1 explicit checks in PreProcessNode.execute()
- No `_extra_security_gate_input()` override needed (default framework PII scan on `user_input`/`validated_input` is sufficient)

### S-3 (Output Gate) — PostProcessNode

The caller receives `formatted_output or result`, which decides how this boundary has
to be written: an error return that omits `formatted_output` publishes the un-gated
inner text through the fallback, and an EMPTY `formatted_output` is falsy and re-opens
the same fallback.

Two invariants are enforced on the assembled report, each with its own audit event:

1. **No credential-shaped string.** The screen is the UNION of the framework's own
   `detect_credentials_in_value` and this template's assignment-form markers
   (`password=`, `api_key=`, `aws_access_key`, …). Measured, the two sets are disjoint
   in both directions — the framework describes credential FORMATS and matches none of
   the assignment forms, and the local markers match none of the formats — so either one
   alone would be narrower than the gate this template advertises.
2. **The report has this template's own structure**: the three fixed headings in order,
   table rows only for risk codes the template defines, and no heading it did not write.
   This is checked on the assembled text, independently of the node that rendered it.

**On a violation**: status ERROR, every output-bearing field present and empty, and a
non-empty notice carrying a reason code from a closed set. The error log is not part of
that notice — it carries node-authored text and, wherever a node interpolates a caught
exception, whatever that exception carried, so publishing it would not be a closed error
contract whatever redaction were applied first.

The producing node (`StructuredSummaryFormatNode`) runs the same shared screen on the
text it assembles. Both call one module (`src/services/screens.py`), so the two can never
enforce different rules.

The framework's own `@final _security_gate_output()` also runs after every `execute()`.
This template does not override `_extra_security_gate_output()`; the domain checks live
in `execute()` where their result can be contained rather than raised.

### Output invariant — the monetary precision grid does not apply

This template renders no monetary AGGREGATE. The one currency figure it reports is a
value quoted from the source document, and rounding a quoted figure would state a number
the document does not — ¥12bn would become ¥0. The invariant enforced instead is the one
above: the structure of the report is the template's, and the numbers in it are the
document's own, bounded and finite.

### S-4 (Audit Logging)
- `emit_trace_event(event, payload, state)` called POSITIONALLY in every node `execute()`
- Events: `ret_c2_303.<node_name>.<event>` (e.g. `ret_c2_303.ir_document_parse.complete`)
- No `node_start`/`node_complete`/`node_error` — `BaseNode.__call__()` emits these automatically

### S-5 (Credential Scan)
- `[build-system].requires = ["setuptools==68.2.2"]` (exact pin, no range)
- No credentials or API keys in state fields or hardcoded in node code

## Framework Utilization

### Shared Components Used
- [x] InvocationContext — forwarded to the inner graph by the framework's GraphNode
- [x] S-1: input validation in the entry adapter and PreProcessNode
- [x] S-3: shared screen (`src/services/screens.py`) at the producing node and the boundary
- [x] S-4: `emit_trace_event(event, payload, state)` — positional form in every node execute()
- [x] AgentStatus enum constants (never plain strings) [A1]

### Caller context does not cross the subgraph boundary by itself

The framework's `GraphNode.execute()` invokes the subgraph with the user input alone and
passes no context channel, so the inner graph cannot see `input_context` however carefully
the outer graph validated it. `src/graph/context_bridge.py` carries the already-validated
context across that one call: the outer `extract_input()` stashes it immediately before
the framework invokes the subgraph, and the inner graph's `_extra_initial_state()` seeds
it into the inner state. Node `execute()` methods take no config argument, so state
seeding is the only route a caller-declared value can travel into a domain node.

### Runtime configuration

`config/agent.yaml` carries identity only. Runtime parameters live in `config/config.yaml`,
which the registry loads and passes as `Graph(config=...)`. The standalone entry point
loads the same file through `src/services/runtime_config.py` — constructing the graph bare
would leave every declared value inert on that deployment while the registry deployment
honoured it.

### Composition Pattern

- **Pattern**: Cat 2 nested (outer AgentBaseGraph + GraphNode → inner BaseGraph domain workflow)
- **Main slot**: `IRSummarizationWorkflowGraphNode(GraphNode)` → `IRSummarizationDomainGraph(BaseGraph)`
- **Error propagation strategy**: propagate (SubgraphError re-raised on inner graph failure)
- **Inner graph path**: `src/graph/domain_workflow_graph.py`

## Import Isolation Confirmation
- [x] Template does not import agenticstar-platform SDK (Level 0)
- [x] Import targets: `framework.*` and `shared.*` only (no `agents/base/` required)
- [x] No `from agenticstar` / `import agenticstar` in `src/`

## Class Name Alignment

| Location | Value |
|----------|-------|
| `src/graph/graph.py` | `class RetailEarningsCallIRSummarizationAgent(AgentBaseGraph)` |
| `config/agent.yaml` | `class: "src.graph.graph.RetailEarningsCallIRSummarizationAgent"` |
| `src/api/server.py` | `from src.graph.graph import RetailEarningsCallIRSummarizationAgent` |

`tests/integration/test_manifest_identity_alignment.py` holds the manifest's identity
fields and the entry point's provisioned identity to each other by reading both, and
pins `namespace` to `lower(industry)`. The entry point previously provisioned secrets
under the lowercased template id while the manifest declared `ret`, which has no
boot-time symptom and splits one agent's secrets across two stores the first time a
secret is declared.

## Design Decision Record

| Decision | Option A | Option B | Chosen | Rationale |
|----------|----------|----------|--------|-----------|
| L1 base type | AgentBaseGraph | AutonomousBaseGraph | AgentBaseGraph | Fixed pipeline; domain steps deterministic, no LLM reasoning loop |
| Composition pattern | Cat 1 flat | Cat 2 nested (GraphNode) | Cat 2 nested | Multiple domain steps (5 inner nodes) require encapsulated pipeline |
| Inner graph parent | BaseGraph | AgentBaseGraph | BaseGraph | Fully custom topology; no inner backbone slots needed |
| Complex state fields | dict/list | Optional[str] JSON | Optional[str] JSON | ADR-005: msgpack-safe flat TypedDict |
| Inner node trust | INTERNAL | ANONYMOUS | ANONYMOUS | review finding 5: inner nodes use ANONYMOUS; external gate lives on backbone pre_process |
| Quoted document text | reproduce verbatim | quote and neutralise | quote and neutralise | A document is caller text; reproducing it verbatim lets it add headings and table rows to the report |
| Credential screen | framework detector only | union with local markers | union | The two pattern sets are disjoint in both directions; either alone is a narrowing |
| Out-of-range figure | render as found | report as absent | report as absent | A figure the agent cannot read as a finite bounded number is not a figure it will state |

## Known framework behaviour this agent works around

The framework's personal-name masking treats a title-case phrase as a person's name, so
an English phrase inside a document can reach the pipeline as `[MASKED]`. Observed on this
template's own path: a document line containing the words *Regulatory Risk Signals* was
masked before any template code ran. This is not template-fixable. It does not affect the
report's own headings, which are the template's constants rather than document text, and
Japanese IR prose — the primary corpus — is largely unaffected. A fork indexing English
proper nouns should expect it.
