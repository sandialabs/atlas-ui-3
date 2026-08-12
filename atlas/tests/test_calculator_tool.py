"""End-to-end tests for the calculator MCP tool's evaluate() entry point.

``test_safe_math_eval.py`` covers the evaluator in isolation. This file
exercises the tool as the MCP server actually exposes it, including the
bounded wrappers around the growth functions in its name table -- those live
in the tool, not the evaluator, because a function in the name table is opaque
to the evaluator that calls it.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

_MAIN = Path(__file__).resolve().parents[1] / "mcp" / "calculator" / "main.py"


def _load_calculator():
    spec = importlib.util.spec_from_file_location("calculator_main", _MAIN)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def evaluate():
    module = _load_calculator()
    tool = module.evaluate
    # FastMCP wraps the function; reach the original when it does.
    return getattr(tool, "fn", tool)


def _result(payload):
    return payload["results"].get("result")


def _is_error(payload):
    return payload["meta_data"]["is_error"]


# --- the tool still does arithmetic ---------------------------------------

@pytest.mark.parametrize(
    "expression,expected",
    [
        ("2 + 3 * 4", 14),
        ("sqrt(pow(3, 2) + pow(4, 2))", 5.0),
        ("factorial(5)", 120),
        ("log10(100)", 2.0),
        ("comb(5, 2)", 10),
        ("perm(5, 2)", 20),
        ("gcd(12, 18)", 6),
        ("2 ** 32", 4294967296),
    ],
)
def test_expressions_evaluate(evaluate, expression, expected):
    payload = evaluate(expression)
    assert _is_error(payload) is False
    assert _result(payload) == expected


def test_three_argument_pow_still_works(evaluate):
    """Modular pow is cheap regardless of exponent, so it must not be bounded."""
    payload = evaluate("pow(9, 999999, 7)")
    assert _is_error(payload) is False
    assert _result(payload) == pow(9, 999999, 7)


# --- code execution is refused --------------------------------------------

def test_reported_rce_payload_is_refused(evaluate, tmp_path):
    marker = tmp_path / "pwned"
    payload = evaluate(
        "[c for c in ().__class__.__mro__[1].__subclasses__() "
        f"if c.__name__=='_wrap_close'][0].__init__.__globals__['system']('touch {marker}')"
    )
    assert _is_error(payload) is True
    assert not marker.exists()


# --- resource exhaustion is refused ---------------------------------------

@pytest.mark.parametrize(
    "expression",
    [
        "9 ** 999999",
        "9**9**9",
        "((9**999)**999)**999",
        "2 ** (500 + 501)",
        "(10**1000)**1000",
        "pow(9, 999999)",
        "pow(9, 99999999)",
        "factorial(999999)",
        "factorial(3000000)",
        "comb(999999, 500000)",
        "perm(999999, 500000)",
        "1 << 99999999",
        "1 << 10000000000",
    ],
)
def test_expensive_expressions_are_refused(evaluate, expression):
    """Each of these must return an error quickly rather than compute."""
    payload = evaluate(expression)
    assert _is_error(payload) is True


# --- the result must always be returnable ---------------------------------

@pytest.mark.parametrize(
    "expression",
    [
        "(2**1000)**15",                    # cheap to compute, too long to encode
        "factorial(1000)*factorial(1000)",  # multiplication is deliberately unbounded
    ],
)
def test_unserializable_results_are_reported_as_tool_errors(evaluate, expression):
    """An int past CPython's int->str cap must not escape as a success payload.

    Otherwise the ValueError fires in the MCP layer after evaluate() returns,
    breaking the is_error contract the docstring promises on every path.
    """
    payload = evaluate(expression)
    assert _is_error(payload) is True
    assert payload["meta_data"]["reason"] == "result_too_large"
    json.dumps(payload)  # must not raise


def test_every_payload_this_tool_returns_is_json_serializable(evaluate):
    expressions = [
        "2 + 2", "divmod(7, 2)", "modf(3.5)", "1 < 2", "sqrt(2)",
        "(2**1000)**15", "factorial(1000)*factorial(1000)",
        "9**9**9", "().__class__", "1 / 0", "sqrt(-1)", "1+" * 200 + "1",
    ]
    for expression in expressions:
        json.dumps(evaluate(expression))


def test_large_but_encodable_result_still_succeeds(evaluate):
    """The guard must not reject results that serialize fine."""
    payload = evaluate("2 ** 1000")
    assert _is_error(payload) is False
    assert _result(payload) == 2**1000


def test_oversized_expression_is_refused(evaluate):
    payload = evaluate("1+" * 200 + "1")
    assert _is_error(payload) is True
    assert payload["meta_data"]["reason"] == "too_long"


def test_error_payload_keeps_the_contract_shape(evaluate):
    payload = evaluate("().__class__")
    assert _is_error(payload) is True
    assert "error" in payload["results"]
    assert payload["results"]["expression"] == "().__class__"
    assert "elapsed_ms" in payload["meta_data"]
