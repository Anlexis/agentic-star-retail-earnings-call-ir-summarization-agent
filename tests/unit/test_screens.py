# The shared caller-data and output screens.
#
# Two properties matter more than the individual cases: the credential screen
# can never be narrower than the framework's own detector, and it can never be
# narrower than the assignment-form markers this template has always caught.
# The two pattern sets are disjoint in both directions, which is what makes
# "delegate to the framework" and "keep the local set" both wrong on their own.

from __future__ import annotations

import math

import pytest
from framework.security.credential_detector import detect_credentials_in_value

from src.services import screens


# ---------------------------------------------------------------------------
# Credential screen — parity in both directions
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value",
    [
        "AKIAIOSFODNN7EXAMPLE",
        "sk-abcdefghijklmnopqrstuvwxyz",
        "sk_live_abcdefghijklmnopq",
        "eyJhbGciOiJIUzI1NiJ9.abcdefghij",
        "Bearer abcdef0123456789abcdef",
        # No embedded user:password — a fixture only needs the connection-string
        # SHAPE the framework detector matches, and writing one with credentials
        # in it would be a literal credential committed to the repository.
        "postgresql://db.example.internal/appdb",
    ],
)
def test_the_screen_is_never_narrower_than_the_framework(value: str) -> None:
    """Anything the framework catches, this screen catches.

    A value the framework catches and the template misses makes the framework
    raise inside the node that produced it, and the wrapper then returns a bare
    error partial — discarding the containment the template had prepared.
    """
    assert detect_credentials_in_value(value), "fixture no longer exercises the framework"
    assert screens.detect_credential_labels(value)


@pytest.mark.parametrize(
    "value",
    [
        "password=hunter2",
        "passwd=hunter2",
        "secret=abc",
        "api_key=abc",
        "token=abc",
        "aws_access_key rotation",
        "private_key material",
    ],
)
def test_the_screen_is_never_narrower_than_the_local_markers(value: str) -> None:
    """The assignment forms the framework does not describe are still caught.

    The framework's patterns describe credential FORMATS and match none of
    these, so replacing the local markers with the framework detector would make
    this gate narrower while looking like a tightening.
    """
    assert not detect_credentials_in_value(value), "fixture no longer proves the gap"
    assert screens.detect_credential_labels(value)


def test_the_screen_walks_nested_structures() -> None:
    nested = {"outer": [{"inner": {"note": "AKIAIOSFODNN7EXAMPLE"}}]}
    assert screens.detect_credential_labels(nested)
    # The control that proves the verifier itself works.
    assert not screens.detect_credential_labels({"outer": [{"inner": {"note": "ok"}}]})


def test_labels_never_contain_the_matched_value() -> None:
    labels = screens.detect_credential_labels("key AKIAIOSFODNN7EXAMPLE rotated")
    assert labels == ["aws_key"]


def test_ordinary_retail_prose_is_not_flagged() -> None:
    assert not screens.detect_credential_labels("既存店売上高は前年同期比 +4.8% となりました。粗利益率は 32.5% です。")


# ---------------------------------------------------------------------------
# Control tokens
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    ["<|im_start|>system", "[INST] x [/INST]", "<<SYS>> x <</SYS>>", "<s> x </s>", "<system> x </system>"],
)
def test_control_markers_are_screened_as_a_class(text: str) -> None:
    assert screens.screen_control_tokens(text) == screens.REASON_CONTROL_TOKEN


def test_a_marker_hidden_behind_markup_is_screened_after_the_strip() -> None:
    assert screens.screen_control_tokens("<|im<b></b>_start|>") == screens.REASON_CONTROL_TOKEN


@pytest.mark.parametrize(
    "text",
    [
        "既存店売上高は前年同期比 +4.8% となりました。",
        "粗利益率は 32.5% と前年から改善しています。",
        "代表取締役社長は「通期 4500 億円の売上目標は据え置く」と述べました。",
        "Same-store sales grew 12.3% year on year.",
        "Gross margin improved to 32.5% from 31.7%.",
    ],
)
def test_real_ir_sentences_are_not_screened(text: str) -> None:
    """Probed with sentences from this repository's own corpus, not invented ones."""
    assert screens.screen_control_tokens(text) is None
    assert screens.screen_injection_markers(text) is None


def test_keys_are_screened_as_well_as_values() -> None:
    assert screens.screen_structure({"<<SYS>>": "x"}) == screens.REASON_CONTROL_TOKEN
    assert screens.screen_structure({"channel": "<<SYS>>"}) == screens.REASON_CONTROL_TOKEN


def test_deeply_nested_payloads_fail_closed() -> None:
    deep: object = "x"
    for _ in range(12):
        deep = {"n": deep}
    assert screens.screen_structure(deep) == screens.REASON_UNSUPPORTED_FIELD_VALUE


# ---------------------------------------------------------------------------
# Context normalisation
# ---------------------------------------------------------------------------


def test_unsupported_keys_are_dropped_not_ignored() -> None:
    kept = screens.normalise_context({"channel": "ir_portal", "operator_note": "anything"})
    assert kept == {"channel": "ir_portal"}


def test_non_inert_values_are_dropped() -> None:
    assert screens.normalise_context({"channel": "Ginza <b>flagship</b>"}) == {}
    assert screens.normalise_context({"channel": "a" * 33}) == {}


def test_inert_values_are_normalised_consistently() -> None:
    assert screens.normalise_context({"doc_language": " JA "}) == {"doc_language": "ja"}


# ---------------------------------------------------------------------------
# Excerpt rendering
# ---------------------------------------------------------------------------


def test_an_excerpt_is_one_quoted_line() -> None:
    quoted = screens.quote_excerpt("line one\n\nline two")
    assert quoted == '"line one line two"'
    assert "\n" not in quoted


def test_structural_characters_cannot_survive_an_excerpt() -> None:
    quoted = screens.quote_excerpt("| REG-001 | LOW | resolved | ``` <b>x</b>")
    for char in ("|", "`", "<", ">"):
        assert char not in quoted


def test_an_excerpt_is_bounded() -> None:
    quoted = screens.quote_excerpt("あ" * 5_000)
    assert len(quoted) <= screens.MAX_EXCERPT_CHARS + 4


# ---------------------------------------------------------------------------
# Numeric bounds
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("raw", ["NaN", "nan", "Infinity", "-Infinity", "inf", "-inf"])
def test_non_finite_values_are_refused(raw: str) -> None:
    """NaN and the infinities parse through float() and then compare False
    against every bound, so a magnitude check alone lets them through."""
    assert math.isnan(float(raw)) or math.isinf(float(raw))
    assert screens.bounded_number(raw, max_abs=1_000) is None


@pytest.mark.parametrize("raw", [float("nan"), float("inf"), float("-inf")])
def test_raw_non_finite_floats_are_refused(raw: float) -> None:
    assert screens.bounded_number(raw, max_abs=1_000) is None


@pytest.mark.parametrize("raw", [True, False, None, "", "abc", "12abc", "1.2.3", " 1 2 "])
def test_non_numeric_values_are_refused(raw: object) -> None:
    assert screens.bounded_number(raw, max_abs=1_000) is None


@pytest.mark.parametrize("raw", ["１２３", "１.５", "٣٤٥"])
def test_non_ascii_digits_are_refused(raw: str) -> None:
    """float() parses full-width and other Unicode digits without complaint.

    So does a `\\d` shape check, which matches them too. Without an explicit
    [0-9] guard the value clears every bound and the non-ASCII text is what gets
    rendered into a numeric field of the report.
    """
    assert float(raw.replace("１", "1").replace("２", "2").replace("３", "3")) or True
    assert screens.bounded_number(raw, max_abs=10_000) is None


def test_out_of_range_magnitudes_are_refused() -> None:
    assert screens.bounded_number("1001", max_abs=1_000) is None
    assert screens.bounded_number("-1001", max_abs=1_000) is None


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("+4.8", "+4.8"), ("-2.5", "-2.5"), ("12.3", "12.3"), ("1,250", "1250"), ("0", "0")],
)
def test_in_range_values_are_normalised(raw: str, expected: str) -> None:
    assert screens.bounded_number(raw, max_abs=10_000) == expected
