#!/usr/bin/env python3
"""
Calculator MCP Server using FastMCP
Provides mathematical operations through MCP protocol.
"""

import math
import time
from typing import Any, Dict, Union

from fastmcp import FastMCP

# Initialize the MCP server
mcp = FastMCP("Calculator")


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
    - Only safe mathematical functions are allowed
    - No access to file system, network, or dangerous operations
    - Sandboxed evaluation environment

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
          "results": {"operation": "evaluate", "expression": str, "result": float},
          "meta_data": {"is_error": bool, "elapsed_ms": float, "reason": str}
        }
    """
    start = time.perf_counter()
    expression_str = str(expression)
    meta: Dict[str, Any] = {}

    # Safety check length
    if len(expression_str) > 200:
        meta.update({"is_error": True, "reason": "too_long"})
        return {
            "results": {"error": "Expression too long", "expression": expression_str},
            "meta_data": _finalize_meta(meta, start)
        }

    allowed_names = {
        # Built-ins
        "abs": abs, "round": round, "min": min, "max": max, "sum": sum,
        "pow": pow, "divmod": divmod,
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
        "factorial": math.factorial, "comb": math.comb, "perm": math.perm, "gcd": math.gcd, "lcm": math.lcm,
        # Float checks
        "isfinite": math.isfinite, "isinf": math.isinf, "isnan": math.isnan
    }

    try:
        result = eval(expression_str, {"__builtins__": {}}, allowed_names)
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
    mcp.run()