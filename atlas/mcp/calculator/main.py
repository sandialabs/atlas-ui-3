#!/usr/bin/env python3
"""
Calculator MCP Server using FastMCP
Provides mathematical operations through MCP protocol.
"""

import math
import time
from typing import Any, Dict, Union

from atlas.mcp_shared.safe_math_eval import (
    MAX_EXPRESSION_LENGTH,
    guard_exponent,
    guard_operand_size,
    safe_eval_math,
)
from atlas.mcp_shared.server_factory import create_stdio_server

# Initialize the MCP server
mcp = create_stdio_server("Calculator")


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
    "abs": abs, "round": round, "min": min, "max": max, "sum": sum,
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
        payload = {
            "operation": "evaluate",
            "expression": expression_str,
            "result": result
        }
        meta.update({"is_error": False})
        return {"results": payload, "meta_data": _finalize_meta(meta, start)}
    except Exception as e:  # noqa: BLE001 - broad for safe tool boundary
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
