"""Tests for the AST-based expression evaluator used by the calculator MCP.

The evaluator replaced ``eval(expr, {"__builtins__": {}}, names)``, which was
escapable: emptying ``__builtins__`` does not stop attribute access from
walking to an imported module's globals. The escape cases below are the
regression tests for that -- they must raise, never execute.
"""

from __future__ import annotations

import math

import pytest

from atlas.mcp_shared.safe_math_eval import (
    MAX_EXPRESSION_LENGTH,
    UnsafeExpressionError,
    guard_exponent,
    guard_operand_size,
    safe_eval_math,
)

NAMES = {
    "abs": abs, "round": round, "min": min, "max": max, "sum": sum,
    "pow": pow, "divmod": divmod,
    "pi": math.pi, "e": math.e, "tau": math.tau, "inf": math.inf, "nan": math.nan,
    "sin": math.sin, "cos": math.cos, "sqrt": math.sqrt, "log10": math.log10,
    "factorial": math.factorial, "hypot": math.hypot, "floor": math.floor,
    "isnan": math.isnan,
}


# --- the escapes that motivated this module -------------------------------

# Each of these evaluates to arbitrary code execution under the old
# eval-with-empty-builtins approach. The first is the payload confirmed
# working against the shipped calculator.
ESCAPES = [
    pytest.param(
        "[c for c in ().__class__.__mro__[1].__subclasses__() "
        "if c.__name__=='_wrap_close'][0].__init__.__globals__['system']('id')",
        id="os_system_via_wrap_close",
    ),
    pytest.param("().__class__", id="attribute_on_literal"),
    pytest.param("abs.__globals__", id="attribute_on_allowed_function"),
    pytest.param("().__class__.__mro__[1].__subclasses__()", id="subclasses_walk"),
    pytest.param("[x for x in (1, 2)]", id="list_comprehension"),
    pytest.param("(lambda: 1)()", id="lambda"),
    pytest.param("__import__('os')", id="import_builtin"),
    pytest.param("open('/etc/passwd')", id="open_builtin"),
    pytest.param("eval('1')", id="nested_eval"),
    pytest.param("{}['a']", id="subscript"),
    pytest.param("'abc'", id="string_literal"),
    pytest.param("f'{abs}'", id="fstring"),
    pytest.param("[1, 2][0]", id="list_index"),
    pytest.param("1 if abs else 2", id="conditional_expression"),
    pytest.param("(y := 1)", id="walrus"),
]


@pytest.mark.parametrize("expression", ESCAPES)
def test_escape_attempts_are_rejected(expression):
    with pytest.raises(UnsafeExpressionError):
        safe_eval_math(expression, NAMES)


def test_confirmed_payload_does_not_execute(tmp_path):
    """The reported payload must not reach os.system, even if reshaped."""
    marker = tmp_path / "pwned"
    payload = (
        "[c for c in ().__class__.__mro__[1].__subclasses__() "
        f"if c.__name__=='_wrap_close'][0].__init__.__globals__['system']('touch {marker}')"
    )
    with pytest.raises(UnsafeExpressionError):
        safe_eval_math(payload, NAMES)
    assert not marker.exists()


# --- the arithmetic that must keep working --------------------------------

@pytest.mark.parametrize(
    "expression,expected",
    [
        ("2 + 3 * 4", 14),
        ("(2 + 3) * 4", 20),
        ("7 // 2", 3),
        ("7 % 2", 1),
        ("2 ** 10", 1024),
        ("-5", -5),
        ("+5", 5),
        ("~5", -6),
        ("10 / 4", 2.5),
        ("6 & 3", 2),
        ("6 | 3", 7),
        ("6 ^ 3", 5),
        ("1 << 4", 16),
        ("16 >> 2", 4),
        ("abs(-3)", 3),
        ("round(3.14159, 2)", 3.14),
        ("min(4, 2, 8)", 2),
        ("max(4, 2, 8)", 8),
        ("pow(3, 2)", 9),
        ("factorial(5)", 120),
        ("log10(100)", 2.0),
        ("sqrt(pow(3, 2) + pow(4, 2))", 5.0),
        ("hypot(3, 4)", 5.0),
        ("floor(3.9)", 3),
        ("divmod(7, 2)", (3, 1)),
        ("1 < 2", True),
        ("1 < 2 < 3", True),
        ("1 < 2 > 5", False),
        ("2 == 2", True),
        ("2 != 2", False),
    ],
)
def test_arithmetic_still_evaluates(expression, expected):
    assert safe_eval_math(expression, NAMES) == expected


def test_constants_resolve():
    assert safe_eval_math("pi", NAMES) == pytest.approx(math.pi)
    assert safe_eval_math("sin(pi/2)", NAMES) == pytest.approx(1.0)
    assert safe_eval_math("isnan(nan)", NAMES) is True


# --- input validation -----------------------------------------------------

def test_expression_over_length_limit_is_rejected():
    with pytest.raises(UnsafeExpressionError, match="too long"):
        safe_eval_math("1+" * MAX_EXPRESSION_LENGTH + "1", NAMES)


def test_empty_expression_is_rejected():
    with pytest.raises(UnsafeExpressionError, match="empty"):
        safe_eval_math("   ", NAMES)


def test_non_string_expression_is_rejected():
    with pytest.raises(UnsafeExpressionError, match="must be a string"):
        safe_eval_math(42, NAMES)


def test_syntax_error_is_reported_as_unsafe_expression():
    with pytest.raises(UnsafeExpressionError, match="Could not parse"):
        safe_eval_math("2 +", NAMES)


def test_unknown_name_is_rejected():
    with pytest.raises(UnsafeExpressionError, match="Unknown name"):
        safe_eval_math("some_undefined_name + 1", NAMES)


def test_unknown_function_is_rejected():
    with pytest.raises(UnsafeExpressionError, match="Unknown function"):
        safe_eval_math("nope(1)", NAMES)


def test_calling_a_non_callable_name_is_rejected():
    with pytest.raises(UnsafeExpressionError, match="Unknown function"):
        safe_eval_math("pi(1)", NAMES)


def test_keyword_arguments_are_rejected():
    with pytest.raises(UnsafeExpressionError, match="Keyword arguments"):
        safe_eval_math("round(3.14159, ndigits=2)", NAMES)


def test_argument_unpacking_is_rejected():
    with pytest.raises(UnsafeExpressionError, match="unpacking"):
        safe_eval_math("max(*(1, 2))", NAMES)


def test_boolean_operators_are_rejected():
    with pytest.raises(UnsafeExpressionError):
        safe_eval_math("1 and 2", NAMES)


def test_huge_literal_exponent_is_rejected():
    """A four-token expression must not be able to hang the server."""
    with pytest.raises(UnsafeExpressionError, match="Exponent too large"):
        safe_eval_math("9**999999", NAMES)


def test_nested_huge_exponent_is_rejected():
    with pytest.raises(UnsafeExpressionError, match="Exponent too large"):
        safe_eval_math("2 * (9 ** 999999)", NAMES)


def test_computed_exponent_is_rejected():
    """The exponent is a BinOp, so a literals-only check would read past it."""
    with pytest.raises(UnsafeExpressionError, match="Exponent too large"):
        safe_eval_math("9**9**9", NAMES)


def test_chained_exponentiation_is_rejected():
    """Each exponent is under the cap; the result still grows without bound."""
    with pytest.raises(UnsafeExpressionError, match="too large"):
        safe_eval_math("(10**1000)**1000", NAMES)


def test_huge_left_shift_is_rejected():
    with pytest.raises(UnsafeExpressionError, match="too large"):
        safe_eval_math("1 << 99999999", NAMES)


def test_modest_exponent_still_works():
    assert safe_eval_math("2 ** 32", NAMES) == 4294967296


def test_modest_shift_still_works():
    assert safe_eval_math("1 << 20", NAMES) == 1048576


def test_float_exponent_is_not_size_checked():
    """Float powers resolve in constant time, so the size guard skips them.

    CPython raises OverflowError rather than returning inf; either way it
    returns immediately and cannot exhaust memory, and the caller surfaces the
    arithmetic error. What matters here is that the guard does not reject it.
    """
    with pytest.raises(OverflowError):
        safe_eval_math("1.5 ** 999999.0", NAMES)


def test_native_arithmetic_errors_propagate():
    """Division by zero is a maths error, not a safety violation."""
    with pytest.raises(ZeroDivisionError):
        safe_eval_math("1 / 0", NAMES)


def test_guard_exponent_is_exported_for_callers_exposing_pow():
    """pow() is opaque to the evaluator, so callers must bound it themselves."""
    with pytest.raises(UnsafeExpressionError, match="Exponent too large"):
        guard_exponent(9, 999999)
    guard_exponent(9, 100)  # must not raise


def test_guard_operand_size_bounds_growth_functions():
    with pytest.raises(UnsafeExpressionError, match="Argument too large"):
        guard_operand_size(999999)
    assert guard_operand_size(10) == 10


def test_names_mapping_is_the_only_reachable_scope():
    """A name absent from the table is unreachable even if it exists globally."""
    with pytest.raises(UnsafeExpressionError, match="Unknown name"):
        safe_eval_math("math", NAMES)
    with pytest.raises(UnsafeExpressionError, match="Unknown name"):
        safe_eval_math("safe_eval_math", NAMES)
