"""AgentCore Platform v1.0 — RET-C2-303 caller-data and output screens"""

# One place for the rules that decide what caller data may enter the pipeline
# and what may leave it. Both the entry adapter and the nodes import from here,
# so the two sides cannot drift into enforcing different rules — a divergence
# between an input screen and an output screen is how a value the producing
# node blocked reaches the caller through a different path.
#
# Every reason this module returns is a fixed label from a closed set. Caller
# text, matched values and exception text are never part of a reason, because a
# reason travels to the caller and a matched value is exactly what must not.

from __future__ import annotations

import math
import re
from typing import Any

from framework.security.credential_detector import detect_credentials_in_value

# ---------------------------------------------------------------------------
# Reason codes — the closed set. Nothing outside this tuple reaches a caller.
# ---------------------------------------------------------------------------

REASON_EMPTY_INPUT = "empty_input"
REASON_INPUT_TOO_LARGE = "input_too_large"
REASON_CONTROL_TOKEN = "control_token_detected"
REASON_INJECTION_MARKER = "injection_marker_detected"
REASON_CREDENTIAL_SHAPE = "credential_shape_detected"
REASON_UNSUPPORTED_FIELD_VALUE = "unsupported_field_value"
REASON_UPSTREAM_FAILURE = "upstream_failure"
REASON_OUTPUT_STRUCTURE = "output_structure_violation"

REASON_CODES: tuple[str, ...] = (
    REASON_EMPTY_INPUT,
    REASON_INPUT_TOO_LARGE,
    REASON_CONTROL_TOKEN,
    REASON_INJECTION_MARKER,
    REASON_CREDENTIAL_SHAPE,
    REASON_UNSUPPORTED_FIELD_VALUE,
    REASON_UPSTREAM_FAILURE,
    REASON_OUTPUT_STRUCTURE,
)

# ---------------------------------------------------------------------------
# Input screens
# ---------------------------------------------------------------------------

# Chat-template control markers, screened as a CLASS rather than as phrases.
# Measured against the installed framework: it scores `<|im_start|>` and
# `[INST]` as high-confidence and returns nothing at all for `<<SYS>>`, so a
# template that relies on the framework alone admits the `<<SYS>>` form. A
# phrase-based screen misses the whole class, because the payload carries no
# recognisable phrase — the marker is the attack.
_CONTROL_TOKEN_RE = re.compile(
    r"<\|[^|>\n]{0,64}\|>"  # <|im_start|>, <|endoftext|>, ...
    r"|\[/?INST\]"  # [INST] ... [/INST]
    r"|<</?SYS>>"  # <<SYS>> ... <</SYS>>
    r"|</?s>"  # <s> ... </s>
    r"|</?system>",  # <system> ... </system>
    re.IGNORECASE,
)

# Markup that a splicing attack hides a directive inside: `ig<b>nore ...` is not
# a control marker raw, and becomes one once the markup is stripped. Screening
# only after a strip loses the marker forms; screening only before loses the
# spliced ones. Both passes run.
_MARKUP_TAG_RE = re.compile(r"<[^<>\n]{1,64}>")

# Script / template-execution markers. Retained from the shipped screen: these
# have no place in an earnings-call transcript and the class is narrow enough
# not to fire on retail IR prose.
_INJECTION_MARKERS: tuple[str, ...] = (
    "{{",
    "}}",
    "<script",
    "javascript:",
    "data:text/html",
    "__import__",
    "os.system",
    "eval(",
)

# Values a caller may put on the context channel are locked to an inert
# identifier alphabet, because they render into the report. Free text there is
# caller-controlled output injection whatever else the pipeline does.
_INERT_IDENTIFIER_RE = re.compile(r"^[a-z0-9_-]{1,32}$")

# Upper bound on the document the entry point will accept, in characters.
MAX_INPUT_SIZE_CHARS: int = 2_000_000

# The only context keys this agent consumes. Anything else is DROPPED rather
# than ignored: an ignored key still travels on the context channel, and the
# framework's first node returns that channel verbatim into its own result,
# where the output gate scans it and fails the run before any template code has
# a chance to reject it readably.
SUPPORTED_CONTEXT_KEYS: tuple[str, ...] = ("channel", "doc_language")


def screen_control_tokens(text: str) -> str | None:
    """Return a reason code when *text* carries a chat-template control marker."""
    if not isinstance(text, str) or not text:
        return None
    if _CONTROL_TOKEN_RE.search(text):
        return REASON_CONTROL_TOKEN
    stripped = _MARKUP_TAG_RE.sub("", text)
    if stripped != text and _CONTROL_TOKEN_RE.search(stripped):
        return REASON_CONTROL_TOKEN
    return None


def screen_injection_markers(text: str) -> str | None:
    """Return a reason code when *text* carries a script/template execution marker."""
    if not isinstance(text, str) or not text:
        return None
    lowered = text.lower()
    if any(marker in lowered for marker in _INJECTION_MARKERS):
        return REASON_INJECTION_MARKER
    stripped = _MARKUP_TAG_RE.sub("", lowered)
    if stripped != lowered and any(marker in stripped for marker in _INJECTION_MARKERS):
        return REASON_INJECTION_MARKER
    return None


def screen_structure(value: Any, *, depth: int = 0) -> str | None:
    """Screen a parsed payload depth-first, KEYS included.

    Keys are screened as well as values because a hostile field name is caller
    data too, and a JSON `\\u` escape cannot evade a scan that runs after the
    parse rather than over the wire format.
    """
    if depth > 8:
        return REASON_UNSUPPORTED_FIELD_VALUE
    if isinstance(value, str):
        return screen_control_tokens(value) or screen_injection_markers(value)
    if isinstance(value, dict):
        for key, nested in value.items():
            if isinstance(key, str):
                reason = screen_control_tokens(key) or screen_injection_markers(key)
                if reason:
                    return reason
            reason = screen_structure(nested, depth=depth + 1)
            if reason:
                return reason
        return None
    if isinstance(value, (list, tuple)):
        for item in value:
            reason = screen_structure(item, depth=depth + 1)
            if reason:
                return reason
    return None


def is_inert_identifier(value: Any) -> bool:
    """True when *value* is safe to render into the report unquoted."""
    return isinstance(value, str) and bool(_INERT_IDENTIFIER_RE.match(value))


def normalise_context(raw: Any) -> dict[str, str]:
    """Keep the supported context keys whose values are inert; drop everything else.

    Dropping rather than ignoring is the point: see SUPPORTED_CONTEXT_KEYS.
    """
    if not isinstance(raw, dict):
        return {}
    kept: dict[str, str] = {}
    for key in SUPPORTED_CONTEXT_KEYS:
        value = raw.get(key)
        if isinstance(value, str) and is_inert_identifier(value.strip().lower()):
            kept[key] = value.strip().lower()
    return kept


# ---------------------------------------------------------------------------
# Credential screen — the UNION of two pattern sets, never either one alone
# ---------------------------------------------------------------------------

# The framework's detector describes credential FORMATS. Measured against the
# installed wheel, it matches AKIA…, sk-…, sk_live_…, eyJ… (JWT), Bearer … and
# database URIs — and matches none of the assignment forms below. The shipped
# local set matched exactly the assignment forms and none of the formats. The
# two sets are disjoint in both directions, so delegating to either one alone
# makes the gate NARROWER than it was while looking like a tightening.
_LOCAL_CREDENTIAL_MARKERS: tuple[tuple[str, str], ...] = (
    ("password=", "assigned_secret"),
    ("passwd=", "assigned_secret"),
    ("secret=", "assigned_secret"),
    ("api_key=", "assigned_secret"),
    ("token=", "assigned_secret"),
    ("aws_access_key", "key_material_reference"),
    ("private_key", "key_material_reference"),
)


def detect_credential_labels(value: Any) -> list[str]:
    """Closed-set credential labels found in *value*, framework set first.

    Returns labels only. The matched text is never returned, so a caller-facing
    refusal built from this can name what kind of thing was found without
    reproducing the thing itself.
    """
    labels: set[str] = {finding["type"] for finding in detect_credentials_in_value(value)}
    for text in _iter_strings(value):
        lowered = text.lower()
        for marker, label in _LOCAL_CREDENTIAL_MARKERS:
            if marker in lowered:
                labels.add(label)
    return sorted(labels)


def _iter_strings(value: Any, depth: int = 0) -> list[str]:
    if depth > 8:
        return []
    if isinstance(value, str):
        return [value]
    out: list[str] = []
    if isinstance(value, dict):
        for nested in value.values():
            out.extend(_iter_strings(nested, depth + 1))
    elif isinstance(value, (list, tuple)):
        for item in value:
            out.extend(_iter_strings(item, depth + 1))
    return out


# ---------------------------------------------------------------------------
# Rendering — caller text is quoted, never reproduced as report structure
# ---------------------------------------------------------------------------

# Characters that carry structural meaning in the rendered report. A document
# excerpt is caller-controlled, so reproducing it verbatim lets the document
# decide how the report reads: a pipe makes a table row, a backtick opens or
# closes a fence, a raw tag renders as markup.
_STRUCTURAL_SUBSTITUTIONS = str.maketrans({"|": "/", "`": "'", "<": "‹", ">": "›"})

_WHITESPACE_RUN_RE = re.compile(r"\s+")

# Upper bound on one rendered excerpt, in characters.
MAX_EXCERPT_CHARS: int = 200


def quote_excerpt(text: str) -> str:
    """Render caller document text as a single quoted line.

    Collapsing the whitespace guarantees the excerpt occupies one line, and the
    surrounding quotes guarantee that line cannot begin with a heading, list or
    table marker. Together with the substitutions above, no document can make
    its own text read as part of the report's structure.
    """
    collapsed = _WHITESPACE_RUN_RE.sub(" ", text).strip()
    collapsed = collapsed.translate(_STRUCTURAL_SUBSTITUTIONS)
    if len(collapsed) > MAX_EXCERPT_CHARS:
        collapsed = collapsed[:MAX_EXCERPT_CHARS].rstrip() + "…"
    return f'"{collapsed}"'


# ---------------------------------------------------------------------------
# Numeric bounds
# ---------------------------------------------------------------------------


# The shape a value must have before it is parsed. Written with an explicit
# [0-9] rather than \d, because \d also matches full-width digits — and so does
# float(), which parses "１２３" to 123.0 without complaint. Without this guard a
# document could state a figure in full-width digits, satisfy every bound, and
# have the full-width text rendered into a numeric field of the report.
_ASCII_NUMBER_RE = re.compile(r"^[+-]?[0-9]+(?:\.[0-9]+)?$")


def bounded_number(raw: Any, *, max_abs: float) -> str | None:
    """Return *raw* normalised when it is a finite number within ±max_abs.

    NaN and ±Infinity parse through float() and then compare False against
    every bound, so a magnitude check written the obvious way passes them
    silently. Testing for finiteness first is what makes this fail closed.
    """
    if raw is None or isinstance(raw, bool):
        return None
    if isinstance(raw, float) and not math.isfinite(raw):
        return None
    text = str(raw).replace(",", "").replace("，", "").strip()
    if not _ASCII_NUMBER_RE.match(text):
        return None
    parsed = float(text)
    if not math.isfinite(parsed) or abs(parsed) > max_abs:
        return None
    return text
