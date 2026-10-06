# Retail Earnings Call & IR Summarization Agent

AI agent for summarizing retail earnings calls and investor relations documents, built with Agentic Star.

> **Category**: Cat 2 (a domain-specific pipeline for one job-to-be-done)
> **Industry**: Retail
> **Template ID**: RET-C2-303

## Overview

A retailer's quarterly disclosure arrives as prose: an earnings-call transcript,
a results presentation, an investor-relations deck. The numbers an analyst needs
are in there, but they are in sentences — comparable-store sales in one
paragraph, gross margin in another, the technology-investment figure somewhere in
the management commentary.

This agent reads one such document and returns a structured summary of it:

- **Financial metrics** — comparable-store sales growth, gross margin, inventory
  turns, and the AI/DX investment figure, each as the number the document
  actually states.
- **Management forward guidance** — executive commentary, full-year targets and
  strategic-initiative statements, quoted from the document.
- **Regulatory risk signals** — a table of the Japanese retail regulatory topics
  the document touches, from a fixed set the agent defines: fair-trade, consumer
  protection, supply-chain regulation, labour compliance, cross-border trade and
  sustainability reporting.

Two properties are worth knowing before you adapt it.

**The report's structure belongs to the agent, not to the document.** A document
is caller-controlled text. Quoted passages are rendered as single quoted lines
with their structural characters substituted, and the output boundary checks the
assembled report independently of the code that rendered it — so a document
cannot add a heading, a table row or a risk verdict of its own.

**Numbers are read, bounded, and otherwise reported as absent.** Every extracted
figure is parsed as a finite number within a range appropriate to its field. A
value the agent cannot read that way is reported as not stated rather than
rendered.

This is an agent template built with the **AGENTIC STAR** development platform and the
**AgentCore Framework**. It is intended to be taken as a starting point: fork it, adapt it to
your own data and policies, and run it inside your own AGENTIC STAR deployment.

## Requirements

**This template does not run standalone.** It requires:

| Requirement | Notes |
|---|---|
| **AGENTIC STAR platform** | The agent connects to the platform at start-up. Without it, start-up fails immediately (see *Behaviour without the platform* below). Deployment guides and API documentation: [AGENTIC STAR Developers](https://developers.fd.agenticstar.tm.softbank.jp/) |
| **AgentCore Framework** (`agenticstar-agentcore`) | Installed from PyPI as a dependency. |
| Python | >=3.11 |

```bash
pip install -e .
```

### Behaviour without the platform

The framework is designed to run **only** on AGENTIC STAR. There is no fallback or degraded
mode. If the platform is unreachable or the SDK version does not match, the agent fails at graph
compile / start-up preflight rather than starting in a partially working state. This is
intentional — a half-running agent is worse than one that refuses to start.

## Quick Start

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
python -m pytest tests/ -v
```

Tests run without a platform connection. Running the agent itself does not.

## Calling it

`POST /invoke` takes the document as `input`, with an optional `input_context`
carrying `channel` and `doc_language`. Both context values are restricted to
short lowercase identifiers, because both are rendered into the report.

```json
{
  "input": "株式会社サンプルリテール 2026年3月期 第2四半期 決算説明会 ...",
  "input_context": { "channel": "ir_portal", "doc_language": "ja" }
}
```

The entry point authenticates the caller with a bearer token from the
environment (`INVOKE_AUTH_TOKEN`, or `STG_INTERNAL_RUNNER_TOKEN` for an internal
deployment credential). A request carrying a credential-shaped string, a
chat-template control marker, or a context value that is not an identifier is
refused with `400` naming the field and a reason code — never echoing the value.

`deploy/invoke_payload.json` holds a working request; it is generated from the
test fixture, so it and the test suite describe the same contract.

## Project Structure

```
src/          agent implementation (nodes, services, schemas)
tests/        unit, integration and boundary tests
config/       agent configuration
docs/         design and operational documentation
```

See `docs/` for the design documentation and the test specification.

## Customising

1. Adjust `config/` for your own environment and policies.
2. Replace the extraction patterns in `src/nodes/` with the terms your own
   documents use — they are Japanese and English retail conventions today.
3. Replace the regulatory topic set in `src/nodes/regulatory_risk_flag_node.py`
   with the regime you report against. The output boundary renders a table row
   only for a code defined there, so adding a topic means adding it in that one
   place.
4. Re-run the test suite.

## License

MIT — see [LICENSE](LICENSE).

## Status of this repository

This template is published **as is**, by its individual author, under the MIT license. It carries
**no warranty and no support commitment**, and no organisation stands behind its behaviour or
fitness for any purpose. Issues and pull requests may or may not receive a response; that is at
the sole discretion of the repository owner.
