#!/usr/bin/env python3
"""
Calculator MCP Server using FastMCP
Provides mathematical operations through MCP protocol.
"""

import logging
import math
import sys
import time
from typing import Any, Dict, Union

from atlas.mcp_shared.safe_math_eval import (
    MAX_EXPRESSION_LENGTH,
    UnsafeExpressionError,
    guard_exponent,
    guard_operand_size,
    safe_eval_math,
)
from atlas.mcp_shared.server_factory import create_stdio_server

logger = logging.getLogger(__name__)

# Initialize the MCP server
mcp = create_stdio_server("Calculator")


# Ratio of decimal digits to bits, used to size an int without converting it
# to a string -- which is the very operation we are checking is possible.
_DIGITS_PER_BIT = 0.30103


def _result_is_returnable(value: Any) -> bool:
    """Return False if this result cannot be encoded in the tool's response.

    Anything that fails here would otherwise raise in the MCP serialization
    layer *after* evaluate() has returned a success payload, so the failure
    escapes the documented ``is_error`` contract entirely and surfaces as a
    broken response rather than a tool error. Three ways that happened:

    - **Oversized ints.** CPython caps int-to-str conversion
      (``sys.get_int_max_str_digits()``, 4300 by default). The evaluator's
      size bounds do not cover this -- they are set for compute cost, and
      ``(2**1000)**15`` and ``factorial(1000)*factorial(1000)`` are both cheap
      and both too long to encode. Multiplication is deliberately unbounded
      for numbers, being linear.
    - **Non-finite floats.** ``inf`` and ``nan`` are reachable as constants
      and from overflow. ``json.dumps`` emits bare ``Infinity``/``NaN``, which
      is not valid JSON and is rejected by strict parsers.
    - **Complex numbers.** Not JSON-encodable at all.

    This is written as a whole-value property rather than a list of known-bad
    expressions, so a new route to any of them is caught without a new case.
    """
    limit = sys.get_int_max_str_digits()

    pending = [value]
    while pending:
        item = pending.pop()
        if isinstance(item, tuple):  # divmod() and modf() return pairs
            pending.extend(item)
        elif isinstance(item, bool):
            continue
        elif isinstance(item, int):
            # limit <= 0 means the host disabled the cap entirely.
            if limit > 0 and item.bit_length() * _DIGITS_PER_BIT + 1 > limit:
                return False
        elif isinstance(item, float):
            if not math.isfinite(item):
                return False
        else:
            # Complex, or anything else a future name-table entry returns.
            return False
    return True


def _bounded_pow(base, exponent, modulus=None):
    """``pow`` with the same size bound the ``**`` operator carries.

    Without this, ``pow(9, 999999)`` reaches the identical big-int blowup that
    ``9 ** 999999`` is stopped from reaching -- the evaluator bounds operators
    it interprets, not functions it was handed. Three-argument ``pow`` is
    modular and cheap regardless of exponent, so it skips the check.
    """
    if modulus is not None:
        return pow(base, exponent, modulus)
    guard_exponent(base, exponent)
    return pow(base, exponent)


def _bounded_round(number, ndigits=None):
    """``round`` with its ndigits bounded.

    A large negative ndigits is superlinear on ints -- `round(5, -32000000)`
    takes tens of seconds and `round(5, -999999999)` does not return. Same
    class as the pow/factorial wrappers, reached through a different argument.
    """
    if ndigits is None:
        return round(number)
    return round(number, guard_operand_size(ndigits))


def _bounded_factorial(n):
    """``math.factorial`` bounded so a small argument cannot hang the server."""
    return math.factorial(guard_operand_size(n))


def _bounded_comb(n, k):
    """``math.comb`` bounded on both arguments."""
    return math.comb(guard_operand_size(n), guard_operand_size(k))


def _bounded_perm(n, k=None):
    """``math.perm`` bounded on both arguments."""
    if k is None:
        return math.perm(guard_operand_size(n))
    return math.perm(guard_operand_size(n), guard_operand_size(k))


# The complete set of names an expression may reference. Anything outside this
# table is unreachable -- see atlas/mcp_shared/safe_math_eval.py for why that
# guarantee holds. Module-level so the table is built once, not per call.
#
# The growth functions are wrapped: the evaluator bounds the operators it
# interprets, but a function in this table is opaque to it, so anything that
# can turn a short argument into a huge number is bounded here.
ALLOWED_NAMES = {
    # Built-ins
    "abs": abs, "round": _bounded_round, "min": min, "max": max, "sum": sum,
    "pow": _bounded_pow, "divmod": divmod,
    # Constants
    "pi": math.pi, "e": math.e, "tau": math.tau, "inf": math.inf, "nan": math.nan,
    # Trigonometric
    "sin": math.sin, "cos": math.cos, "tan": math.tan,
    "asin": math.asin, "acos": math.acos, "atan": math.atan, "atan2": math.atan2,
    "hypot": math.hypot, "degrees": math.degrees, "radians": math.radians,
    # Hyperbolic
    "sinh": math.sinh, "cosh": math.cosh, "tanh": math.tanh,
    "asinh": math.asinh, "acosh": math.acosh, "atanh": math.atanh,
    # Exponential & logarithmic
    "exp": math.exp, "sqrt": math.sqrt, "log": math.log, "log10": math.log10, "log2": math.log2,
    # Rounding & numeric ops
    "ceil": math.ceil, "floor": math.floor, "trunc": math.trunc, "modf": math.modf,
    "copysign": math.copysign, "fabs": math.fabs, "fmod": math.fmod,
    # Combinatorics & number theory
    "factorial": _bounded_factorial, "comb": _bounded_comb, "perm": _bounded_perm,
    "gcd": math.gcd, "lcm": math.lcm,
    # Float checks
    "isfinite": math.isfinite, "isinf": math.isinf, "isnan": math.isnan
}


def to_float(value: Union[str, int, float]) -> float:
    """Convert input to float, handling strings and numbers."""
    try:
        return float(value)
    except (ValueError, TypeError):  # pragma: no cover - simple helper
        raise ValueError(f"Cannot convert '{value}' to a number")


def to_int(value: Union[str, int, float]) -> int:
    """Convert input to int, handling strings and numbers."""
    try:
        return int(float(value))  # Convert to float first to handle "5.0" -> 5
    except (ValueError, TypeError):  # pragma: no cover - simple helper
        raise ValueError(f"Cannot convert '{value}' to an integer")

@mcp.tool
def evaluate(expression: str) -> Dict[str, Any]:
    """Safely evaluate a wide range of mathematical expressions with comprehensive mathematical functions.

    This calculator tool provides secure mathematical computation capabilities including:

    **Basic Operations:**
    - Arithmetic: +, -, *, /, //, %, **
    - Built-in functions: abs(), round(), min(), max(), sum(), pow(), divmod()

    **Mathematical Constants:**
    - pi, e, tau, inf, nan

    **Trigonometric Functions:**
    - sin(), cos(), tan(), asin(), acos(), atan(), atan2()
    - degrees(), radians(), hypot()

    **Hyperbolic Functions:**
    - sinh(), cosh(), tanh(), asinh(), acosh(), atanh()

    **Exponential & Logarithmic:**
    - exp(), sqrt(), log(), log10(), log2()

    **Rounding & Numeric Operations:**
    - ceil(), floor(), trunc(), modf(), copysign(), fabs(), fmod()

    **Combinatorics & Number Theory:**
    - factorial(), comb(), perm(), gcd(), lcm()

    **Float Validation:**
    - isfinite(), isinf(), isnan()

    **Security Features:**
    - Expression length limited to 200 characters
    - Parsed and walked as an AST; `eval` is never used
    - Attribute access, subscripting, and comprehensions are rejected outright
    - Only the mathematical names listed above are reachable

    **Not Supported** (these return an error rather than a result). Where a
    form used to work, the replacement is given -- these are the spellings to
    use:
    - Keyword arguments. Write `round(3.14159, 2)`, NOT `round(3.14159, ndigits=2)`
    - List and dict syntax. Write `sum((1, 2, 3))` with parentheses,
      NOT `sum([1, 2, 3])` with brackets
    - String literals and f-strings
    - `and`, `or`, `not`, `in`, `is` -- this evaluates numbers, not logic
    - Attribute access (`x.y`), indexing (`x[0]`), lambdas, and comprehensions
    - Complex numbers such as `2j`
    - Results that cannot be returned: an integer beyond roughly 4300 digits,
      or a non-finite result such as `inf` or `nan`
    - Arguments large enough to hang the server: an exponent, `ndigits`, or
      `factorial`/`comb`/`perm` argument above 1000, or a tuple repetition
      producing more than 1000 elements

    **Examples:**
    - Basic: "2 + 3 * 4" → 14
    - Trigonometry: "sin(pi/2)" → 1.0
    - Logarithms: "log10(100)" → 2.0
    - Combinatorics: "factorial(5)" → 120
    - Complex: "sqrt(pow(3, 2) + pow(4, 2))" → 5.0

    Args:
        expression: Mathematical expression to evaluate (string, max 200 chars)

    Returns:
        MCP contract shape with results and timing metadata:
        {
          "results": {"operation": "evaluate", "expression": str, "result": number},
          "meta_data": {"is_error": bool, "elapsed_ms": float, "reason": str}
        }

        `result` is whatever the expression evaluates to: usually an int or
        float, but `divmod()` yields a tuple and a comparison yields a bool.
        On error, `results` carries `error` and `expression` instead.
    """
    start = time.perf_counter()
    expression_str = str(expression)
    meta: Dict[str, Any] = {}

    if len(expression_str) > MAX_EXPRESSION_LENGTH:
        meta.update({"is_error": True, "reason": "too_long"})
        return {
            "results": {"error": "Expression too long", "expression": expression_str},
            "meta_data": _finalize_meta(meta, start)
        }

    try:
        result = safe_eval_math(expression_str, ALLOWED_NAMES)
        if not _result_is_returnable(result):
            meta.update({"is_error": True, "reason": "result_not_returnable"})
            return {
                "results": {
                    "error": "Result cannot be returned (too large, or not a finite number)",
                    "expression": expression_str,
                },
                "meta_data": _finalize_meta(meta, start)
            }
        payload = {
            "operation": "evaluate",
            "expression": expression_str,
            "result": result
        }
        meta.update({"is_error": False})
        return {"results": payload, "meta_data": _finalize_meta(meta, start)}
    except UnsafeExpressionError as e:
        # A refused construct is worth a log line: a sandbox-escape attempt
        # and a typo both end up here, and without this they are
        # indistinguishable from each other and invisible to an operator.
        logger.warning(
            "calculator rejected expression: %s (expression=%r)",
            e,
            expression_str[:MAX_EXPRESSION_LENGTH],
        )
        meta.update({"is_error": True, "reason": "rejected_expression"})
        return {
            "results": {"error": f"Evaluation error: {e}", "expression": expression_str},
            "meta_data": _finalize_meta(meta, start)
        }
    except Exception as e:  # noqa: BLE001 - broad for safe tool boundary
        # Ordinary maths errors: ZeroDivisionError, OverflowError, the
        # ValueError from sqrt(-1). Not a safety event, so not logged.
        meta.update({"is_error": True, "reason": type(e).__name__})
        return {
            "results": {"error": f"Evaluation error: {e}", "expression": expression_str},
            "meta_data": _finalize_meta(meta, start)
        }


def _finalize_meta(meta: Dict[str, Any], start: float) -> Dict[str, Any]:
    """Attach timing info and return meta_data dict."""
    meta = dict(meta)  # shallow copy
    meta["elapsed_ms"] = round((time.perf_counter() - start) * 1000, 3)
    return meta



if __name__ == "__main__":
    mcp.run(show_banner=False)
